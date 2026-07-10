from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from yqy_bot.core.config import load_project_config
from yqy_bot.background.worker import BackgroundJob
from yqy_bot.core.models import GateDecision, IntentDecision, NapCatSettings
from yqy_bot.core.pipeline import ChatPipeline
from yqy_bot.context.prompt import PromptBuilder
from yqy_bot.qq.emoji import BUILTIN_FACES
from yqy_bot.qq.parser import parse_message_input
from yqy_bot.qq.sender import build_message_segments


class FakeLLMRouter:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {"intent": 0, "chat": 0, "background": 0}

    def available(self, role: str) -> bool:
        return role in {"intent", "chat", "background"}

    async def chat_json(
        self,
        role: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.calls[role] = self.calls.get(role, 0) + 1
        if role == "intent":
            return {
                "should_reply": True,
                "intent": "direct_question",
                "priority": 0.9,
                "reply_style": "short_chat",
                "need_reason_model": False,
                "need_vision_model": False,
                "need_emoji": False,
                "reason_short": "用户在私聊中询问身份",
            }
        if role == "chat":
            return {
                "text": "我是 YHarvest。",
                "send_face": False,
                "face_id": "",
                "send_mface": False,
                "mface": None,
                "send_image": False,
                "image_url": "",
                "at_user_id": "",
                "reply_to_message_id": "",
            }
        if role == "background":
            return {
                "summary": "",
                "user_profile": {},
                "group_profile": {},
                "memories": [],
                "reflections": [],
                "emoji": [],
            }
        raise RuntimeError(f"unexpected role: {role}")


@dataclass
class FakeNapCatClient:
    custom_face_payload: dict[str, Any]
    custom_face_error: Exception | None = None
    message_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    group_history_payload: dict[str, Any] = field(default_factory=dict)
    settings: NapCatSettings = field(default_factory=NapCatSettings)
    calls: dict[str, int] = field(
        default_factory=lambda: {
            "fetch_custom_face_detail": 0,
            "get_msg": 0,
            "get_group_msg_history": 0,
            "send_msg": 0,
        }
    )
    sent_payloads: list[dict[str, Any]] = field(default_factory=list)

    async def call_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls[action] = self.calls.get(action, 0) + 1
        if action == "fetch_custom_face_detail":
            if self.custom_face_error is not None:
                raise self.custom_face_error
            return self.custom_face_payload
        if action == "get_msg":
            message_id = str(payload.get("message_id", ""))
            return self.message_payloads.get(
                message_id, {"status": "ok", "retcode": 0, "data": {}}
            )
        if action == "get_group_msg_history":
            return self.group_history_payload
        if action in {"send_msg", "send_private_msg", "send_group_msg"}:
            self.sent_payloads.append({"action": action, "payload": dict(payload)})
            return {
                "status": "ok",
                "retcode": 0,
                "data": {"message_id": payload.get("message_id", "sent")},
            }
        if action in {"get_group_member_info", "get_group_member_list"}:
            return {"status": "ok", "retcode": 0, "data": {}}
        return {"status": "ok", "retcode": 0, "data": {}}


@dataclass
class FakeSender:
    sent: list[dict[str, Any]]

    async def send(self, parsed, response) -> None:
        self.sent.append(
            {
                "chat_key": parsed.chat_key,
                "segments": build_message_segments(response),
                "text": response.text,
            }
        )


def _fresh_pipeline(
    temp_db: Path,
    napcat_payload: dict[str, Any],
    *,
    custom_face_error: Exception | None = None,
    message_payloads: dict[str, dict[str, Any]] | None = None,
    group_history_payload: dict[str, Any] | None = None,
    enable_history_backfill: bool = False,
) -> tuple[ChatPipeline, FakeNapCatClient]:
    config = load_project_config(ROOT)
    config.bot.database_path = str(temp_db)
    config.bot.context.enable_history_backfill = enable_history_backfill
    pipeline = ChatPipeline.from_config(config)
    fake_llm = FakeLLMRouter()
    fake_napcat = FakeNapCatClient(
        custom_face_payload=napcat_payload,
        custom_face_error=custom_face_error,
        message_payloads=message_payloads or {},
        group_history_payload=group_history_payload
        or {"status": "ok", "retcode": 0, "data": {}},
    )
    pipeline.bundle.llm_router = fake_llm
    pipeline.bundle.intent_router.llm_router = fake_llm
    pipeline.bundle.response_generator.llm_router = fake_llm
    pipeline.bundle.safety_guard.llm_router = fake_llm
    pipeline.bundle.background.llm_router = fake_llm
    pipeline.bundle.napcat_tools.client = fake_napcat
    return pipeline, fake_napcat


def _count_rows(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> int:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(sql, params).fetchone()
        return int(row[0] if row else 0)
    finally:
        connection.close()


def _fetch_history_payloads(
    db_path: Path, session_id: str, role: str | None = None
) -> list[dict[str, Any]]:
    connection = sqlite3.connect(db_path)
    try:
        if role is None:
            rows = connection.execute(
                "SELECT content_json FROM chat_history WHERE chat_key = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT content_json FROM chat_history WHERE chat_key = ? AND role = ? ORDER BY id ASC",
                (session_id, role),
            ).fetchall()
        payloads: list[dict[str, Any]] = []
        for (content_json,) in rows:
            try:
                payload = json.loads(content_json)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads
    finally:
        connection.close()


def _raw_event(
    *,
    message_id: str,
    self_id: str,
    user_id: str,
    message_type: str,
    group_id: str = "",
    sender_card: str = "",
    sender_nickname: str = "",
    segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "self_id": self_id,
        "user_id": user_id,
        "message_id": message_id,
        "message_type": message_type,
        "sender": {
            "nickname": sender_nickname,
            "card": sender_card,
        },
        "message": segments or [],
    }
    if group_id:
        payload["group_id"] = group_id
    return payload


async def _build_context_prompt(
    pipeline: ChatPipeline,
    parsed,
    *,
    group_heat_state: str = "quiet",
    intent: IntentDecision | None = None,
) -> tuple[Any, str]:
    intent = intent or IntentDecision(should_reply=True, reply_style="normal")
    context = await pipeline.bundle.context_builder.build(
        parsed, intent, group_heat_state=group_heat_state
    )
    prompt = PromptBuilder().build(context)
    return context, prompt


def _background_job(parsed, context=None) -> BackgroundJob:
    return BackgroundJob(
        parsed=parsed,
        gate=GateDecision(allow=True, group_mode="quiet", reason="smoke"),
        intent=IntentDecision(should_reply=False),
        response=None,
        context=context,
    )


async def _run() -> int:
    with tempfile.TemporaryDirectory(prefix="yqy-bot-smoke-") as tmpdir:
        tmpdir_path = Path(tmpdir)

        private_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "private.sqlite3",
            {
                "status": "ok",
                "retcode": 0,
                "data": {
                    "faces": [
                        {
                            "emoji_id": "e1",
                            "emoji_package_id": "p1",
                            "key": "k1",
                            "summary": "偷笑",
                        }
                    ]
                },
            },
        )
        private_pipeline.bundle.config.bot.private_cooldown_seconds = 3
        private_sender = FakeSender(sent=[])
        private_input = {
            "event_id": "evt-private-1",
            "adapter": "onebot11",
            "platform": "qq",
            "raw_event": _raw_event(
                message_id="msg-private-1",
                self_id="10086",
                user_id="10001",
                message_type="private",
                sender_nickname="Alice",
                sender_card="",
                segments=[{"type": "text", "data": {"text": "你好，你是谁"}}],
            ),
        }
        private_result = await private_pipeline.process_message(
            private_input, sender=private_sender
        )
        private_pass = (
            private_result.parsed.session_id == "private:10001"
            and private_result.parsed.sender_display_name == "Alice"
            and private_result.gate.allow
            and private_result.intent.should_reply
            and private_result.response is not None
            and private_result.response.text == "我是 YHarvest。"
            and private_result.assistant_written
            and _count_rows(
                tmpdir_path / "private.sqlite3",
                "SELECT COUNT(*) FROM chat_history WHERE chat_key = 'private:10001'",
            )
            == 2
            and _count_rows(
                tmpdir_path / "private.sqlite3",
                "SELECT COUNT(*) FROM intent_log WHERE chat_key = 'private:10001'",
            )
            == 1
            and len(private_sender.sent) == 1
            and private_sender.sent[0]["segments"]
            == [{"type": "text", "data": {"text": "我是 YHarvest。"}}]
        )

        group_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "group.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        group_sender = FakeSender(sent=[])
        group_input_1 = {
            "event_id": "evt-group-1",
            "adapter": "onebot11",
            "platform": "qq",
            "raw_event": _raw_event(
                message_id="msg-group-1",
                self_id="10086",
                user_id="10002",
                group_id="20001",
                message_type="group",
                sender_nickname="Bob",
                sender_card="BobCard",
                segments=[{"type": "text", "data": {"text": "群里第一条"}}],
            ),
        }
        group_input_2 = {
            "event_id": "evt-group-2",
            "adapter": "onebot11",
            "platform": "qq",
            "raw_event": _raw_event(
                message_id="msg-group-2",
                self_id="10086",
                user_id="10003",
                group_id="20001",
                message_type="group",
                sender_nickname="Carol",
                sender_card="",
                segments=[{"type": "text", "data": {"text": "群里第二条"}}],
            ),
        }
        group_result_1 = await group_pipeline.process_message(
            group_input_1, sender=group_sender
        )
        group_result_2 = await group_pipeline.process_message(
            group_input_2, sender=group_sender
        )
        group_user_payloads = _fetch_history_payloads(
            tmpdir_path / "group.sqlite3", "group:20001", role="user"
        )
        group_pass = (
            group_result_1.parsed.session_id == "group:20001"
            and group_result_2.parsed.session_id == "group:20001"
            and any(
                item.get("sender_display_name") == "BobCard"
                for item in group_user_payloads
            )
            and any(
                item.get("sender_display_name") == "Carol"
                for item in group_user_payloads
            )
            and all(
                item.get("session_id") == "group:20001" for item in group_user_payloads
            )
        )

        at_bot_input = {
            "event_id": "evt-group-at",
            "adapter": "onebot11",
            "platform": "qq",
            "raw_event": _raw_event(
                message_id="msg-group-at",
                self_id="10086",
                user_id="10002",
                group_id="20001",
                message_type="group",
                sender_nickname="Bob",
                sender_card="BobCard",
                segments=[
                    {"type": "at", "data": {"qq": "10086"}},
                    {"type": "text", "data": {"text": " 帮我看看"}},
                ],
            ),
        }
        parsed_at_bot = parse_message_input(at_bot_input)
        at_pass = parsed_at_bot.is_at_bot and parsed_at_bot.session_id == "group:20001"

        reply_input = {
            "event_id": "evt-group-reply",
            "adapter": "onebot11",
            "platform": "qq",
            "raw_event": _raw_event(
                message_id="msg-group-reply",
                self_id="10086",
                user_id="10002",
                group_id="20001",
                message_type="group",
                sender_nickname="Bob",
                sender_card="BobCard",
                segments=[
                    {"type": "reply", "data": {"id": "reply-msg-9"}},
                    {"type": "text", "data": {"text": " 这个呢"}},
                ],
            ),
        }
        parsed_reply = parse_message_input(reply_input)
        reply_pass = (
            parsed_reply.reply_message_id == "reply-msg-9"
            and parsed_reply.session_id == "group:20001"
        )

        emoji_pipeline, emoji_napcat = _fresh_pipeline(
            tmpdir_path / "emoji.sqlite3",
            {
                "status": "ok",
                "retcode": 0,
                "data": {
                    "faces": [
                        {
                            "emoji_id": "e1",
                            "emoji_package_id": "p1",
                            "key": "k1",
                            "summary": "偷笑",
                        }
                    ]
                },
            },
        )
        emoji_sender = FakeSender(sent=[])
        emoji_result = await emoji_pipeline.process_message(
            {
                "event_id": "evt-emoji-1",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-emoji-1",
                    self_id="10086",
                    user_id="10004",
                    message_type="private",
                    sender_nickname="Dave",
                    sender_card="",
                    segments=[{"type": "text", "data": {"text": "来个表情"}}],
                ),
            },
            sender=emoji_sender,
        )
        emoji_pass = (
            emoji_result.response is not None
            and emoji_result.response.send_mface
            and emoji_result.response.mface.get("emoji_id") == "e1"
            and _count_rows(
                tmpdir_path / "emoji.sqlite3",
                "SELECT COUNT(*) FROM emoji_store WHERE emoji_type = 'mface'",
            )
            == 1
            and emoji_napcat.calls["fetch_custom_face_detail"] == 1
            and len(emoji_sender.sent) == 1
            and any(
                segment["type"] == "mface"
                for segment in emoji_sender.sent[0]["segments"]
            )
        )

        empty_pipeline, empty_napcat = _fresh_pipeline(
            tmpdir_path / "empty.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        empty_sender = FakeSender(sent=[])
        empty_result = await empty_pipeline.process_message(
            {
                "event_id": "evt-emoji-2",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-emoji-2",
                    self_id="10086",
                    user_id="10005",
                    message_type="private",
                    sender_nickname="Eve",
                    sender_card="",
                    segments=[{"type": "text", "data": {"text": "来个表情"}}],
                ),
            },
            sender=empty_sender,
        )
        builtin_pass = (
            empty_result.response is not None
            and empty_result.response.send_face
            and empty_result.response.face_id
            in {item["face_id"] for item in BUILTIN_FACES}
            and _count_rows(
                tmpdir_path / "empty.sqlite3", "SELECT COUNT(*) FROM emoji_store"
            )
            == 0
            and empty_napcat.calls["fetch_custom_face_detail"] == 1
            and len(empty_sender.sent) == 1
            and any(
                segment["type"] == "face"
                for segment in empty_sender.sent[0]["segments"]
            )
        )

        error_pipeline, error_napcat = _fresh_pipeline(
            tmpdir_path / "emoji-error.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
            custom_face_error=ConnectionError("napcat unavailable"),
        )
        error_sender = FakeSender(sent=[])
        error_result = await error_pipeline.process_message(
            {
                "event_id": "evt-emoji-error",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-emoji-error",
                    self_id="10086",
                    user_id="10006",
                    message_type="private",
                    sender_nickname="Frank",
                    sender_card="",
                    segments=[{"type": "text", "data": {"text": "你收藏的表情包"}}],
                ),
            },
            sender=error_sender,
        )
        emoji_error_fallback_pass = (
            error_result.response is not None
            and error_result.response.send_face
            and error_result.response.face_id
            in {item["face_id"] for item in BUILTIN_FACES}
            and error_napcat.calls["fetch_custom_face_detail"] == 1
            and len(error_sender.sent) == 1
            and any(
                segment["type"] == "face"
                for segment in error_sender.sent[0]["segments"]
            )
        )

        private_second_result = await private_pipeline.process_message(
            {
                "event_id": "evt-private-2",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-private-2",
                    self_id="10086",
                    user_id="10001",
                    message_type="private",
                    sender_nickname="Alice",
                    sender_card="",
                    segments=[{"type": "text", "data": {"text": "还在吗"}}],
                ),
            },
            sender=private_sender,
        )
        private_cooldown_pass = (
            private_second_result.gate.reason == "send cooldown"
            and not private_second_result.gate.allow
            and not private_second_result.sent
            and not private_second_result.assistant_written
        )

        heat_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "heat.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        heat_states: list[str] = []
        for index in range(1, 22):
            await heat_pipeline.process_message(
                {
                    "event_id": f"evt-heat-{index}",
                    "adapter": "onebot11",
                    "platform": "qq",
                    "raw_event": _raw_event(
                        message_id=f"msg-heat-{index}",
                        self_id="10086",
                        user_id=str(10020 + (index % 3)),
                        group_id="20002",
                        message_type="group",
                        sender_nickname=f"U{index}",
                        sender_card=f"卡{index}",
                        segments=[
                            {"type": "text", "data": {"text": f"群消息 {index}"}}
                        ],
                    ),
                },
                sender=None,
            )
            state = heat_pipeline.bundle.repos.get_cooldown_state("group:20002") or {}
            heat_states.append(str(state.get("heat_state", "quiet")))
        heat_state_pass = (
            "quiet" in heat_states[:2]
            and "active" in heat_states
            and "hot" in heat_states
            and heat_states[-1] == "flood"
        )
        flood_result = await heat_pipeline.process_message(
            {
                "event_id": "evt-heat-flood",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-heat-flood",
                    self_id="10086",
                    user_id="10021",
                    group_id="20002",
                    message_type="group",
                    sender_nickname="U99",
                    sender_card="卡99",
                    segments=[{"type": "text", "data": {"text": "普通消息"}}],
                ),
            },
            sender=None,
        )
        flood_pass = (
            not flood_result.gate.allow
            and not flood_result.sent
            and not flood_result.assistant_written
        )

        profile_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "profile.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        profile_pipeline.bundle.repos.upsert_user_profile(
            "10001",
            {"favorite": "coffee", "traits": ["quiet", "careful"]},
            "### 用户画像\n- favorite: coffee\n- traits: quiet, careful",
            dirty_count=2,
        )
        profile_pipeline.bundle.repos.upsert_group_profile(
            "20001",
            {"topic": "coding", "style": "克制"},
            "### 群画像\n- topic: coding\n- style: 克制",
            message_count_since_update=3,
        )
        profile_private_parsed = parse_message_input(private_input)
        profile_private_context, profile_private_prompt = await _build_context_prompt(
            profile_pipeline,
            profile_private_parsed,
            group_heat_state="quiet",
        )
        profile_private_pass = (
            "### 用户画像" in profile_private_context.user_profile_md
            and "favorite: coffee" in profile_private_prompt
            and "traits: quiet, careful" in profile_private_prompt
        )
        profile_group_parsed = parse_message_input(group_input_1)
        profile_group_context, profile_group_prompt = await _build_context_prompt(
            profile_pipeline,
            profile_group_parsed,
            group_heat_state="active",
        )
        profile_group_pass = (
            "### 群画像" in profile_group_context.group_profile_md
            and "topic: coding" in profile_group_prompt
            and "style: 克制" in profile_group_prompt
            and "群画像" not in profile_private_prompt
        )

        profile_conflict_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "profile-conflict.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        profile_conflict_pipeline.bundle.repos.upsert_user_profile(
            "10001",
            {
                "preferences": ["A"],
                "recent_focus": [],
                "stable_facts": [],
                "boundaries": [],
                "evidence_message_ids": [],
                "needs_review": [],
                "confidence": {},
                "communication_style": "",
            },
            "### 用户画像\n- preferences: 用户喜欢 A\n- recent_focus: 用户最近偏好 A",
            dirty_count=3,
        )
        conflict_profile_input = parse_message_input(
            {
                "event_id": "evt-profile-conflict",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-profile-conflict",
                    self_id="10086",
                    user_id="10001",
                    message_type="private",
                    sender_nickname="Alice",
                    sender_card="",
                    segments=[{"type": "text", "data": {"text": "我不喜欢 A 了"}}],
                ),
            }
        )
        conflict_profile_context, conflict_profile_prompt = await _build_context_prompt(
            profile_conflict_pipeline,
            conflict_profile_input,
            group_heat_state="quiet",
        )
        profile_conflict_pass = (
            "用户喜欢 A" not in conflict_profile_context.user_profile_md
            and "用户最近偏好 A" not in conflict_profile_prompt
            and conflict_profile_context.context_data.get("summary_policy")
            == "old_facts_only"
        )

        summary_conflict_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "summary-conflict.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        summary_conflict_pipeline.bundle.repos.upsert_summary(
            "private:10001",
            "用户喜欢 A；旧事实：项目还在推进",
        )
        original_summary = summary_conflict_pipeline.bundle.repos.get_summary(
            "private:10001"
        )
        summary_conflict_pipeline.bundle.background._update_from_heuristics(
            _background_job(conflict_profile_input)
        )
        summary_after = summary_conflict_pipeline.bundle.repos.get_summary(
            "private:10001"
        )
        summary_conflict_pass = summary_after == original_summary

        profile_hardening_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "profile-hardening.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        noise_input = {
            "event_id": "evt-profile-noise",
            "adapter": "onebot11",
            "platform": "qq",
            "raw_event": _raw_event(
                message_id="msg-profile-noise",
                self_id="10086",
                user_id="10001",
                message_type="private",
                sender_nickname="Alice",
                sender_card="",
                segments=[{"type": "text", "data": {"text": "哈哈哈"}}],
            ),
        }
        noise_parsed = parse_message_input(noise_input)
        for index in range(5):
            noise_parsed_loop = parse_message_input(
                {
                    "event_id": f"evt-profile-noise-{index}",
                    "adapter": "onebot11",
                    "platform": "qq",
                    "raw_event": _raw_event(
                        message_id=f"msg-profile-noise-{index}",
                        self_id="10086",
                        user_id="10001",
                        message_type="private",
                        sender_nickname="Alice",
                        sender_card="",
                        segments=[{"type": "text", "data": {"text": "哈哈哈"}}],
                    ),
                }
            )
            profile_hardening_pipeline.bundle.background._update_from_heuristics(
                _background_job(noise_parsed_loop)
            )
        noise_state = profile_hardening_pipeline.bundle.repos.get_user_profile("10001")
        noise_pass = (
            int(noise_state.get("dirty_count", 0)) == 0 and not noise_state["profile"]
        )

        focus_input = parse_message_input(
            {
                "event_id": "evt-profile-focus",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-profile-focus",
                    self_id="10086",
                    user_id="10001",
                    message_type="private",
                    sender_nickname="Alice",
                    sender_card="",
                    segments=[
                        {"type": "text", "data": {"text": "我最近在重构 QQ 机器人"}}
                    ],
                ),
            }
        )
        profile_hardening_pipeline.bundle.background._update_from_heuristics(
            _background_job(focus_input)
        )
        focus_state = profile_hardening_pipeline.bundle.repos.get_user_profile("10001")
        focus_pass = int(
            focus_state.get("dirty_count", 0)
        ) == 1 and "我最近在重构 QQ 机器人" in json.dumps(
            focus_state["profile"], ensure_ascii=False
        )

        flood_hardening_pipeline, flood_hardening_napcat = _fresh_pipeline(
            tmpdir_path / "flood-hardening.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        flood_context_parsed = None
        for index in range(20):
            flood_event = {
                "event_id": f"evt-flood-{index}",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id=f"msg-flood-{index}",
                    self_id="10086",
                    user_id=str(10020 + (index % 3)),
                    group_id="20099",
                    message_type="group",
                    sender_nickname=f"Flood{index}",
                    sender_card=f"卡{index}",
                    segments=[{"type": "text", "data": {"text": "哈哈哈"}}],
                ),
            }
            flood_context_parsed = parse_message_input(flood_event)
            flood_hardening_pipeline.bundle.group_heat.update(
                flood_context_parsed, now=1000.0 + index
            )
            flood_hardening_pipeline.bundle.background._update_from_heuristics(
                _background_job(flood_context_parsed)
            )
        flood_state = (
            flood_hardening_pipeline.bundle.repos.get_cooldown_state("group:20099")
            or {}
        )
        flood_input = parse_message_input(
            {
                "event_id": "evt-flood-final",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-flood-final",
                    self_id="10086",
                    user_id="10021",
                    group_id="20099",
                    message_type="group",
                    sender_nickname="FloodFinal",
                    sender_card="卡Final",
                    segments=[{"type": "text", "data": {"text": "普通消息"}}],
                ),
            }
        )
        flood_context, _ = await _build_context_prompt(
            flood_hardening_pipeline,
            flood_input,
            group_heat_state=str(flood_state.get("heat_state", "quiet")),
        )
        await flood_hardening_pipeline.bundle.background._update_from_context(
            BackgroundJob(
                parsed=flood_input,
                gate=GateDecision(allow=False, group_mode="flood", reason="flood"),
                intent=IntentDecision(should_reply=False),
                response=None,
                context=flood_context,
            )
        )
        flood_hardening_pass = (
            str(flood_state.get("heat_state", "quiet")) == "flood"
            and flood_hardening_pipeline.bundle.background.llm_router.calls.get(
                "background", 0
            )
            == 0
        )

        multi_group_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "multi-group.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        await multi_group_pipeline.process_message(group_input_1, sender=None)
        await multi_group_pipeline.process_message(group_input_2, sender=None)
        multi_group_parsed = parse_message_input(group_input_2)
        multi_group_context, multi_group_prompt = await _build_context_prompt(
            multi_group_pipeline,
            multi_group_parsed,
            group_heat_state="quiet",
        )
        multi_group_pass = (
            multi_group_context.recent_history
            and multi_group_context.recent_history[-1].chat_key == "group:20001"
            and "[BobCard]" in multi_group_prompt
            and "[Carol]" in multi_group_prompt
        )

        conflict_pipeline, _ = _fresh_pipeline(
            tmpdir_path / "conflict.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
        )
        conflict_pipeline.bundle.repos.add_memory(
            parsed=profile_private_parsed,
            kind="fact",
            content="用户喜欢 A",
            score=0.9,
        )
        conflict_input = {
            "event_id": "evt-conflict-1",
            "adapter": "onebot11",
            "platform": "qq",
            "raw_event": _raw_event(
                message_id="msg-conflict-1",
                self_id="10086",
                user_id="10001",
                message_type="private",
                sender_nickname="Alice",
                sender_card="",
                segments=[{"type": "text", "data": {"text": "我不喜欢 A 了"}}],
            ),
        }
        conflict_parsed = parse_message_input(conflict_input)
        conflict_context, conflict_prompt = await _build_context_prompt(
            conflict_pipeline,
            conflict_parsed,
            group_heat_state="quiet",
        )
        conflict_pass = (
            "我不喜欢 A 了" in conflict_prompt
            and conflict_context.context_data["current_message"]["text"]
            == "我不喜欢 A 了"
            and conflict_context.context_data.get("memory_conflict_policy")
            == "current_message_overrides_prior_memory"
            and "用户喜欢 A" not in conflict_prompt
            and conflict_context.context_data.get("relevant_memories", []) == []
            and "你喜欢 A" not in conflict_prompt
        )

        reply_backfill_pipeline, reply_backfill_napcat = _fresh_pipeline(
            tmpdir_path / "reply-backfill.sqlite3",
            {
                "status": "ok",
                "retcode": 0,
                "data": {},
            },
            message_payloads={
                "reply-msg-9": _raw_event(
                    message_id="reply-msg-9",
                    self_id="10086",
                    user_id="10002",
                    group_id="20001",
                    message_type="group",
                    sender_nickname="Bob",
                    sender_card="BobCard",
                    segments=[{"type": "text", "data": {"text": "被引用的消息"}}],
                )
            },
            enable_history_backfill=True,
        )
        reply_backfill_context, reply_backfill_prompt = await _build_context_prompt(
            reply_backfill_pipeline,
            parsed_reply,
            group_heat_state="quiet",
        )
        reply_backfill_pass = (
            reply_backfill_context.context_data["reference_message"]["message_id"]
            == "reply-msg-9"
            and "被引用的消息" in reply_backfill_prompt
            and reply_backfill_napcat.calls["get_msg"] == 1
        )

        backfill_off_pipeline, backfill_off_napcat = _fresh_pipeline(
            tmpdir_path / "backfill-off.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
            group_history_payload={
                "status": "ok",
                "retcode": 0,
                "data": {
                    "messages": [
                        _raw_event(
                            message_id="hist-1",
                            self_id="10086",
                            user_id="10030",
                            group_id="20003",
                            message_type="group",
                            sender_nickname="HistA",
                            sender_card="",
                            segments=[{"type": "text", "data": {"text": "历史 1"}}],
                        )
                    ]
                },
            },
            enable_history_backfill=False,
        )
        backfill_off_input = parse_message_input(
            {
                "event_id": "evt-backfill-off",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-backfill-off",
                    self_id="10086",
                    user_id="10030",
                    group_id="20003",
                    message_type="group",
                    sender_nickname="HistA",
                    sender_card="",
                    segments=[{"type": "text", "data": {"text": "现在聊什么"}}],
                ),
            }
        )
        backfill_off_pipeline.bundle.repos.add_chat_history(
            parsed=backfill_off_input,
            role="user",
            content=backfill_off_input.text,
            metadata={"segments": backfill_off_input.segments},
        )
        await backfill_off_pipeline.bundle.context_builder.build(
            backfill_off_input,
            IntentDecision(should_reply=True),
            group_heat_state="quiet",
        )
        backfill_off_pass = backfill_off_napcat.calls["get_group_msg_history"] == 0

        backfill_on_pipeline, backfill_on_napcat = _fresh_pipeline(
            tmpdir_path / "backfill-on.sqlite3",
            {"status": "ok", "retcode": 0, "data": {}},
            group_history_payload={
                "status": "ok",
                "retcode": 0,
                "data": {
                    "messages": [
                        _raw_event(
                            message_id="hist-1",
                            self_id="10086",
                            user_id="10031",
                            group_id="20004",
                            message_type="group",
                            sender_nickname="HistB",
                            sender_card="卡B",
                            segments=[{"type": "text", "data": {"text": "历史 1"}}],
                        ),
                        _raw_event(
                            message_id="hist-1",
                            self_id="10086",
                            user_id="10031",
                            group_id="20004",
                            message_type="group",
                            sender_nickname="HistB",
                            sender_card="卡B",
                            segments=[{"type": "text", "data": {"text": "历史 1"}}],
                        ),
                        _raw_event(
                            message_id="hist-2",
                            self_id="10086",
                            user_id="10032",
                            group_id="20004",
                            message_type="group",
                            sender_nickname="HistC",
                            sender_card="",
                            segments=[{"type": "text", "data": {"text": "历史 2"}}],
                        ),
                    ]
                },
            },
            enable_history_backfill=True,
        )
        backfill_on_input = parse_message_input(
            {
                "event_id": "evt-backfill-on",
                "adapter": "onebot11",
                "platform": "qq",
                "raw_event": _raw_event(
                    message_id="msg-backfill-on",
                    self_id="10086",
                    user_id="10033",
                    group_id="20004",
                    message_type="group",
                    sender_nickname="HistD",
                    sender_card="卡D",
                    segments=[{"type": "text", "data": {"text": "现在聊什么"}}],
                ),
            }
        )
        backfill_on_pipeline.bundle.repos.add_chat_history(
            parsed=backfill_on_input,
            role="user",
            content=backfill_on_input.text,
            metadata={"segments": backfill_on_input.segments},
        )
        await backfill_on_pipeline.bundle.context_builder.build(
            backfill_on_input,
            IntentDecision(should_reply=True),
            group_heat_state="quiet",
        )
        backfill_on_count = _count_rows(
            tmpdir_path / "backfill-on.sqlite3",
            "SELECT COUNT(*) FROM chat_history WHERE chat_key = 'group:20004' AND source = 'napcat_history'",
        )
        backfill_on_hist1 = _count_rows(
            tmpdir_path / "backfill-on.sqlite3",
            "SELECT COUNT(*) FROM chat_history WHERE chat_key = 'group:20004' AND message_id = 'hist-1' AND source = 'napcat_history'",
        )
        backfill_on_pass = (
            backfill_on_napcat.calls["get_group_msg_history"] == 1
            and backfill_on_count == 2
            and backfill_on_hist1 == 1
        )
        print(f"private_case: {'PASS' if private_pass else 'FAIL'}")
        print(f"group_case: {'PASS' if group_pass else 'FAIL'}")
        print(f"at_bot_case: {'PASS' if at_pass else 'FAIL'}")
        print(f"reply_case: {'PASS' if reply_pass else 'FAIL'}")
        print(f"emoji_custom_case: {'PASS' if emoji_pass else 'FAIL'}")
        print(f"emoji_builtin_fallback_case: {'PASS' if builtin_pass else 'FAIL'}")
        print(
            f"emoji_error_fallback_case: {'PASS' if emoji_error_fallback_pass else 'FAIL'}"
        )
        print(f"private_cooldown_case: {'PASS' if private_cooldown_pass else 'FAIL'}")
        print(
            f"group_heat_case: {'PASS' if heat_state_pass and flood_pass else 'FAIL'}"
        )
        print(f"user_profile_case: {'PASS' if profile_private_pass else 'FAIL'}")
        print(f"group_profile_case: {'PASS' if profile_group_pass else 'FAIL'}")
        print(f"profile_noise_case: {'PASS' if noise_pass else 'FAIL'}")
        print(f"profile_focus_case: {'PASS' if focus_pass else 'FAIL'}")
        print(f"profile_conflict_case: {'PASS' if profile_conflict_pass else 'FAIL'}")
        print(f"summary_conflict_case: {'PASS' if summary_conflict_pass else 'FAIL'}")
        print(f"group_flood_llm_case: {'PASS' if flood_hardening_pass else 'FAIL'}")
        print(f"group_multi_context_case: {'PASS' if multi_group_pass else 'FAIL'}")
        print(f"conflict_priority_case: {'PASS' if conflict_pass else 'FAIL'}")
        print(f"reply_backfill_case: {'PASS' if reply_backfill_pass else 'FAIL'}")
        print(f"history_backfill_off_case: {'PASS' if backfill_off_pass else 'FAIL'}")
        print(f"history_backfill_on_case: {'PASS' if backfill_on_pass else 'FAIL'}")
        await empty_pipeline.shutdown()
        await error_pipeline.shutdown()
        await emoji_pipeline.shutdown()
        await heat_pipeline.shutdown()
        await profile_pipeline.shutdown()
        await multi_group_pipeline.shutdown()
        await conflict_pipeline.shutdown()
        await reply_backfill_pipeline.shutdown()
        await backfill_off_pipeline.shutdown()
        await backfill_on_pipeline.shutdown()
        await group_pipeline.shutdown()
        await private_pipeline.shutdown()
        return (
            0
            if all(
                [
                    private_pass,
                    group_pass,
                    at_pass,
                    reply_pass,
                    emoji_pass,
                    builtin_pass,
                    emoji_error_fallback_pass,
                    private_cooldown_pass,
                    heat_state_pass,
                    flood_pass,
                    profile_private_pass,
                    profile_group_pass,
                    noise_pass,
                    focus_pass,
                    profile_conflict_pass,
                    summary_conflict_pass,
                    flood_hardening_pass,
                    multi_group_pass,
                    conflict_pass,
                    reply_backfill_pass,
                    backfill_off_pass,
                    backfill_on_pass,
                ]
            )
            else 1
        )


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
