"""搜索策略模块，判断是否需要触发搜索。"""

from __future__ import annotations

import logging
import re

from yqy_bot.core.models import (
    DEFAULT_SEARCH_TRIGGER_KEYWORDS,
    GateDecision,
    IntentDecision,
    SearchDecision,
)

LOGGER = logging.getLogger(__name__)

# URL 正则表达式
URL_PATTERN = re.compile(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w .~%\-+=@&?#()]*")
SEARCH_ACTION_KEYWORDS = [
    "帮我搜一下",
    "帮我查一下",
    "帮我搜索",
    "帮我查",
    "帮我找",
    "联网查一下",
    "联网查",
    "搜一下",
    "查一下",
    "搜索",
    "搜搜",
    "列出",
    "查查",
]
SEARCH_FORMAT_PATTERNS = [
    r"以\s*json\s*的?格式",
    r"用\s*json\s*格式",
    r"json\s*格式",
    r"(列出|输出|返回|整理|给我|写成)",
    r"[一二三四五六七八九十\d]+条",
    r"(对应)?网页的?(url|URL|链接|网址)",
    r"(还要|并且|同时|以及|附上|带上)",
]


def decide_search(
    text: str,
    intent: IntentDecision,
    gate: GateDecision,
    *,
    enabled: bool = True,
) -> SearchDecision:
    """判断是否需要触发搜索。

    搜索触发条件：
    1. 用户明确说：搜一下、查一下、搜索等关键词
    2. 用户消息中包含 URL：触发 web_extract
    3. 意图识别 LLM 明确判断需要搜索
    4. gate_allow=False 时不要搜索
    5. 普通闲聊、低信息消息不要搜索

    Args:
        text: 用户消息文本
        intent: 意图决策对象
        gate: 门控决策对象
        enabled: 搜索功能是否启用

    Returns:
        SearchDecision 对象，包含是否需要搜索、搜索查询等
    """
    # 搜索功能未启用
    if not enabled:
        return SearchDecision(need_search=False, need_extract=False)

    # 门控不允许时不搜索
    if not gate.allow:
        return SearchDecision(need_search=False, need_extract=False)

    text = text.strip()
    if not text:
        return SearchDecision(need_search=False, need_extract=False)

    # 检查 URL 抽取需求
    urls = _extract_urls(text)
    if urls and _should_extract(text):
        return SearchDecision(
            need_extract=True,
            extract_url=urls[0],
            reason="用户消息包含 URL 且表达抽取意图",
        )

    # 检查显式搜索请求
    if _has_explicit_search_request(text):
        query = _sanitize_search_query(_extract_search_query(text))
        return SearchDecision(
            need_search=True,
            search_query=query,
            reason="用户显式请求搜索",
        )

    # 检查意图识别的搜索需求
    if intent.need_search and intent.search_query:
        return SearchDecision(
            need_search=True,
            search_query=_sanitize_search_query(intent.search_query),
            reason=intent.search_reason or "意图识别判断需要搜索",
        )

    # 检查时效性问题
    if _has_time_sensitive_question(text):
        query = _extract_time_sensitive_query(text)
        if query:
            return SearchDecision(
                need_search=True,
                search_query=_sanitize_search_query(query),
                reason="用户询问时效性问题",
            )

    return SearchDecision(need_search=False, need_extract=False)


def _has_explicit_search_request(text: str) -> bool:
    """判断用户是否显式请求搜索。

    Args:
        text: 用户消息文本

    Returns:
        如果包含显式搜索请求则返回 True
    """
    text_lower = text.lower()
    for keyword in DEFAULT_SEARCH_TRIGGER_KEYWORDS:
        if keyword.lower() in text_lower:
            return True
    return False


def _should_extract(text: str) -> bool:
    """判断是否需要抽取 URL 内容。

    当用户消息包含 URL 且表达总结、解释、看看等意图时触发。

    Args:
        text: 用户消息文本

    Returns:
        如果需要抽取则返回 True
    """
    extract_keywords = [
        "总结",
        "解释",
        "看看",
        "帮我看看",
        "看一下",
        "是什么",
        "怎么说",
        "内容",
        "说下",
        "讲讲",
    ]
    return any(kw in text for kw in extract_keywords)


def _extract_urls(text: str) -> list[str]:
    """从文本中提取 URL。

    Args:
        text: 用户消息文本

    Returns:
        URL 列表
    """
    return URL_PATTERN.findall(text)


def _extract_search_query(text: str) -> str:
    """从搜索请求中提取搜索查询。

    Args:
        text: 用户消息文本

    Returns:
        提取的搜索查询字符串
    """
    query = _strip_mentions(text)
    query = re.sub(r"^(你|请|麻烦|帮忙)[，,、\s]*", "", query).strip()

    trigger_match = _find_search_action(query)
    if trigger_match:
        before = query[: trigger_match.start()].strip(" ，,。.!！？?：:;；")
        after = query[trigger_match.end() :].strip(" ，,。.!！？?：:;；")
        query = after or before or query

    query = _remove_response_format_requirements(query)

    query = query.strip()
    # 如果提取后为空，使用原文
    if not query:
        query = _strip_mentions(text)

    # 限制查询长度
    if len(query) > 200:
        query = query[:200]

    return query


def _has_time_sensitive_question(text: str) -> bool:
    """判断用户是否询问时效性问题。

    Args:
        text: 用户消息文本

    Returns:
        如果包含时效性问题则返回 True
    """
    time_keywords = ["最新", "现在", "今天", "今年", "最近", "当前"]
    question_keywords = ["?", "？", "吗", "呢", "多少", "什么", "怎么"]

    has_time = any(kw in text for kw in time_keywords)
    has_question = any(kw in text for kw in question_keywords)

    return has_time and has_question


def _extract_time_sensitive_query(text: str) -> str:
    """从时效性问题中提取搜索查询。

    Args:
        text: 用户消息文本

    Returns:
        提取的搜索查询字符串
    """
    # 直接使用原文作为查询
    query = text.strip()
    if len(query) > 200:
        query = query[:200]
    return query


def _sanitize_search_query(query: str) -> str:
    """清理搜索查询中的群聊噪声。"""
    query = _strip_mentions(query)
    query = query.replace("\u200b", " ")
    for keyword in SEARCH_ACTION_KEYWORDS:
        query = query.replace(keyword, " ")
    query = _remove_response_format_requirements(query)
    query = re.sub(r"\s+", " ", query).strip(" ，,。.!！？?：:;；")
    return query[:200].strip()


def _strip_mentions(text: str) -> str:
    """移除群聊 @ 噪声。"""
    return re.sub(r"@[^\s]+\s*", "", text).strip()


def _find_search_action(text: str) -> re.Match[str] | None:
    """找到真正的搜索动作词，避免把“最新”等查询词当作动作删掉。"""
    matches = [
        match
        for keyword in SEARCH_ACTION_KEYWORDS
        if (match := re.search(re.escape(keyword), text))
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: item.start())


def _remove_response_format_requirements(query: str) -> str:
    """从搜索词中剥离回复格式要求，保留时效词和主题词。"""
    query = re.sub(r"^(然后|并且|同时|再)?[，,、\s]*", "", query)
    for pattern in SEARCH_FORMAT_PATTERNS:
        query = re.sub(pattern, " ", query, flags=re.IGNORECASE)
    query = query.replace("的", " ")
    query = re.sub(r"\s+", " ", query).strip(" ，,。.!！？?：:;；")
    return query
