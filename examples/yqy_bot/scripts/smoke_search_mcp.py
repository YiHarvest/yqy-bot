"""Smoke test for search MCP integration.

Tests:
1. Search trigger: explicit search request
2. URL extract: URL with extraction intent
3. No search trigger: casual chat
4. Gate block: gate_allow=False should not search
5. Search failure: simulate error
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yqy_bot.core.models import (
    GateDecision,
    IntentDecision,
    ParsedMessage,
    SearchMCPSettings,
    SearchResult,
)
from yqy_bot.tools.search_policy import decide_search
from yqy_bot.tools.search_mcp import SearchMCPClient


def _make_parsed(
    text: str,
    *,
    user_id: str = "10001",
    group_id: str = "",
    is_group: bool = False,
) -> ParsedMessage:
    """Create a ParsedMessage for testing."""
    return ParsedMessage(
        event_id="test-event",
        adapter="onebot11",
        platform="qq",
        self_id="738151903",
        user_id=user_id,
        group_id=group_id,
        message_id="test-msg-id",
        message_type="group" if is_group else "private",
        sender_nickname="TestUser",
        sender_card="",
        text=text,
        raw={},
        segments=[],
        raw_segments=[],
        is_at_bot=False,
        mention_user_ids=[],
        has_image=False,
        reply_message_id="",
        sender_display_name="TestUser",
        is_group=is_group,
        is_private=not is_group,
    )


def _make_gate(allow: bool = True, group_mode: str = "quiet") -> GateDecision:
    """Create a GateDecision for testing."""
    return GateDecision(
        allow=allow,
        group_mode=group_mode,
        reason="test",
        is_triggered=allow,
    )


def _make_intent(should_reply: bool = True) -> IntentDecision:
    """Create an IntentDecision for testing."""
    return IntentDecision(
        should_reply=should_reply,
        reply_style="normal",
        need_reason_model=False,
        need_emoji=False,
        confidence=0.8,
        reply_length="normal",
        notes="test",
    )


def test_explicit_search_request() -> None:
    """Test that explicit search requests trigger search."""
    print("Test 1: Explicit search request")
    text = "帮我搜一下 search-engine-tool-mcp 0.4.2"
    parsed = _make_parsed(text)
    gate = _make_gate(allow=True)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=True)

    assert decision.need_search, f"Expected need_search=True, got {decision}"
    assert decision.search_query, f"Expected search_query, got {decision.search_query}"
    assert (
        decision.reason == "用户显式请求搜索"
    ), f"Unexpected reason: {decision.reason}"
    print(
        f"  PASS: need_search={decision.need_search}, query={decision.search_query[:50]}"
    )


def test_search_query_sanitization() -> None:
    """Test that group mentions and trigger words are removed from search queries."""
    print("Test 1b: Search query sanitization")
    text = "@123   查一下gpt最新进展"
    gate = _make_gate(allow=True)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=True)

    assert decision.need_search, f"Expected need_search=True, got {decision}"
    assert (
        "@123" not in decision.search_query
    ), f"Unexpected mention in query: {decision.search_query}"
    assert (
        "查一下" not in decision.search_query
    ), f"Unexpected trigger phrase in query: {decision.search_query}"
    assert (
        "gpt" in decision.search_query.lower()
    ), f"Expected topic retained, got {decision.search_query}"
    print(f"  PASS: sanitized query={decision.search_query}")


def test_search_query_keeps_topic_not_output_format() -> None:
    """Test that output formatting instructions are not used as search terms."""
    print("Test 1c: Search query strips output format instructions")
    text = "@123 你查一下，然后以json的格式列出gpt的最新的三条信息还要对应网页的url"
    gate = _make_gate(allow=True)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=True)

    assert decision.need_search, f"Expected need_search=True, got {decision}"
    assert (
        "gpt" in decision.search_query.lower()
    ), f"Expected topic retained, got {decision.search_query}"
    assert (
        "最新" in decision.search_query
    ), f"Expected recency term retained, got {decision.search_query}"
    assert (
        "json" not in decision.search_query.lower()
    ), f"Unexpected output format in query: {decision.search_query}"
    assert (
        "url" not in decision.search_query.lower()
    ), f"Unexpected URL format request in query: {decision.search_query}"
    print(f"  PASS: sanitized query={decision.search_query}")


def test_url_extraction_request() -> None:
    """Test that URL with extraction intent triggers extract."""
    print("Test 2: URL extraction request")
    text = "总结一下 https://pypi.org/project/search-engine-tool-mcp/0.4.2/"
    parsed = _make_parsed(text)
    gate = _make_gate(allow=True)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=True)

    assert decision.need_extract, f"Expected need_extract=True, got {decision}"
    assert decision.extract_url, f"Expected extract_url, got {decision.extract_url}"
    assert "pypi.org" in decision.extract_url, f"Unexpected URL: {decision.extract_url}"
    print(
        f"  PASS: need_extract={decision.need_extract}, url={decision.extract_url[:60]}"
    )


def test_no_search_trigger() -> None:
    """Test that casual chat does not trigger search."""
    print("Test 3: No search trigger for casual chat")
    text = "哈哈"
    parsed = _make_parsed(text)
    gate = _make_gate(allow=True)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=True)

    assert not decision.need_search, f"Expected need_search=False, got {decision}"
    assert not decision.need_extract, f"Expected need_extract=False, got {decision}"
    print(f"  PASS: need_search={decision.need_search}")


def test_gate_blocked() -> None:
    """Test that gate_allow=False blocks search."""
    print("Test 4: Gate blocked prevents search")
    text = "帮我搜一下 Python 最新版本"
    parsed = _make_parsed(text)
    gate = _make_gate(allow=False)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=True)

    assert (
        not decision.need_search
    ), f"Expected need_search=False when gate blocked, got {decision}"
    print(f"  PASS: need_search={decision.need_search} (gate_allow=False)")


def test_search_disabled() -> None:
    """Test that disabled search does not trigger."""
    print("Test 5: Search disabled")
    text = "帮我搜一下 test"
    parsed = _make_parsed(text)
    gate = _make_gate(allow=True)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=False)

    assert (
        not decision.need_search
    ), f"Expected need_search=False when disabled, got {decision}"
    print(f"  PASS: need_search={decision.need_search} (disabled)")


def test_time_sensitive_question() -> None:
    """Test that time-sensitive questions trigger search."""
    print("Test 6: Time-sensitive question")
    text = "今天天气怎么样？"
    parsed = _make_parsed(text)
    gate = _make_gate(allow=True)
    intent = _make_intent()

    decision = decide_search(text, intent, gate, enabled=True)

    assert (
        decision.need_search
    ), f"Expected need_search=True for time-sensitive, got {decision}"
    print(f"  PASS: need_search={decision.need_search}, reason={decision.reason}")


def test_search_result_model() -> None:
    """Test SearchResult model serialization."""
    print("Test 7: SearchResult model")

    # Success case
    result_ok = SearchResult(
        ok=True,
        type="web_search",
        query="test query",
        provider="auto",
        results=[
            {
                "title": "Result 1",
                "href": "https://example.com/1",
                "abstract": "Abstract 1",
            },
            {
                "title": "Result 2",
                "href": "https://example.com/2",
                "abstract": "Abstract 2",
            },
        ],
    )
    assert result_ok.ok
    assert len(result_ok.results) == 2
    print(f"  PASS: ok result with {len(result_ok.results)} results")

    # Failure case
    result_fail = SearchResult(
        ok=False,
        type="web_search",
        query="test query",
        error="timeout",
        message="搜索超时",
    )
    assert not result_fail.ok
    assert result_fail.error == "timeout"
    print(f"  PASS: failed result with error={result_fail.error}")


def test_search_settings_from_env() -> None:
    """Test SearchMCPSettings from environment variables."""
    print("Test 8: SearchMCPSettings from environment")
    import os

    # Save original env
    original_enabled = os.environ.get("SEARCH_MCP_ENABLED")
    original_command = os.environ.get("SEARCH_MCP_COMMAND")

    try:
        os.environ["SEARCH_MCP_ENABLED"] = "true"
        os.environ["SEARCH_MCP_COMMAND"] = "test-command"

        settings = SearchMCPSettings.from_mapping({})
        assert (
            settings.enabled
        ), f"Expected enabled=True from env, got {settings.enabled}"
        assert (
            settings.command == "test-command"
        ), f"Expected command from env, got {settings.command}"
        print(f"  PASS: enabled={settings.enabled}, command={settings.command}")
    finally:
        # Restore original env
        if original_enabled is not None:
            os.environ["SEARCH_MCP_ENABLED"] = original_enabled
        elif "SEARCH_MCP_ENABLED" in os.environ:
            del os.environ["SEARCH_MCP_ENABLED"]
        if original_command is not None:
            os.environ["SEARCH_MCP_COMMAND"] = original_command
        elif "SEARCH_MCP_COMMAND" in os.environ:
            del os.environ["SEARCH_MCP_COMMAND"]


def test_search_trigger_keywords() -> None:
    """Test all search trigger keywords."""
    print("Test 9: Search trigger keywords coverage")

    trigger_cases = [
        "搜一下 Python",
        "查一下天气",
        "搜索最新新闻",
        "帮我查时间",
        "帮我找电影",
        "联网查股票",
        "最新版本",
        "今天新闻",
        "GitHub release",
    ]

    for text in trigger_cases:
        gate = _make_gate(allow=True)
        intent = _make_intent()
        decision = decide_search(text, intent, gate, enabled=True)
        assert decision.need_search, f"Expected search for: {text}"

    print(f"  PASS: {len(trigger_cases)} trigger keywords tested")


def test_command_parsing() -> None:
    """Test that MCP command strings can include arguments."""
    print("Test 10: MCP command parsing")
    client = SearchMCPClient(
        settings=SearchMCPSettings(
            enabled=True, command="uvx search-engine-tool-mcp --from test"
        ),
    )
    assert client._build_command_args() == [
        "uvx",
        "search-engine-tool-mcp",
        "--from",
        "test",
    ], client._build_command_args()
    print(f"  PASS: args={client._build_command_args()}")


def run_tests() -> int:
    """Run all smoke tests."""
    print("=" * 60)
    print("Smoke Test: Search MCP Integration")
    print("=" * 60)

    tests = [
        test_explicit_search_request,
        test_search_query_sanitization,
        test_search_query_keeps_topic_not_output_format,
        test_url_extraction_request,
        test_no_search_trigger,
        test_gate_blocked,
        test_search_disabled,
        test_time_sensitive_question,
        test_search_result_model,
        test_search_settings_from_env,
        test_search_trigger_keywords,
        test_command_parsing,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
