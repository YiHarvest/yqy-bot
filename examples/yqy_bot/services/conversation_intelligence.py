"""对话智能服务：意图识别、聊天摘要和用户画像更新。"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timezone
from typing import Any

from iamai import LLMClient, LLMConfig
from loguru import logger

from .config_service import MAX_HISTORY_TURNS
from .db import get_connection
from .history import HistoryService
from .relation import RelationshipService


class ConversationIntelligenceService:
    """使用数据库摘要/画像做意图判断，并持续更新会话记忆。"""

    _INTENT_CONCURRENCY = 1
    _PROFILE_CONCURRENCY = 1
    _intent_semaphore = asyncio.Semaphore(_INTENT_CONCURRENCY)
    _profile_semaphore = asyncio.Semaphore(_PROFILE_CONCURRENCY)
    _MAX_RETRIES = 3

    def __init__(self) -> None:
        self._history = HistoryService()
        self._relation = RelationshipService()

    @staticmethod
    def _split_session(session_id: str) -> tuple[str, str]:
        if ":" in session_id:
            group_id, user_id = session_id.split(":", 1)
            return group_id, user_id
        return "", session_id

    @staticmethod
    def _scope_name(session_id: str) -> str:
        group_id, user_id = ConversationIntelligenceService._split_session(session_id)
        if group_id:
            return f"group:{group_id}:user:{user_id}"
        return f"private:user:{user_id}"

    @staticmethod
    def _intent_llm_config() -> LLMConfig:
        """为意图识别构建独立的 LLM 配置。"""
        return LLMConfig.from_mapping(
            {
                "api_key": os.getenv("INTENT_OPENAI_API_KEY", ""),
                "base_url": os.getenv("INTENT_OPENAI_BASE_URL", ""),
                "model": os.getenv("INTENT_OPENAI_MODEL", os.getenv("OPENAI_MODEL", "")),
                "temperature": float(os.getenv("INTENT_OPENAI_TEMPERATURE", "0.2")),
                "max_tokens": int(os.getenv("INTENT_OPENAI_MAX_TOKENS", "300")),
                "timeout": float(os.getenv("INTENT_OPENAI_TIMEOUT", "30")),
            }
        )

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """判断是否为 429 / rate limit 类型错误。"""
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 429:
            return True
        message = str(exc).lower()
        return any(
            keyword in message
            for keyword in ("429", "rate limit", "too many requests", "request rate")
        )

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        """指数退避 + 抖动。"""
        base = min(2 ** attempt, 8)
        return base + random.uniform(0.0, 0.5)

    async def _chat_json_with_retry(
        self,
        config: LLMConfig,
        messages: list[dict[str, str]],
        *,
        label: str,
        retries: int | None = None,
    ) -> dict[str, Any]:
        """带 429 退避重试的 JSON 调用。"""
        attempts = retries or self._MAX_RETRIES
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await LLMClient(config).chat_json(messages)
            except Exception as exc:
                last_exc = exc
                if not self._is_rate_limit_error(exc) or attempt >= attempts - 1:
                    raise
                delay = self._retry_delay(attempt)
                logger.warning(
                    f"{label} 命中 429，{delay:.1f}s 后重试（{attempt + 1}/{attempts}）"
                )
                await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"{label} 调用失败，但未捕获到具体异常")

    def get_summary(self, session_id: str) -> str:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT summary FROM chat_summary WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return str(row[0]) if row and row[0] else ""
        finally:
            conn.close()

    def get_profile(self, session_id: str) -> str:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT profile FROM user_profile WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return str(row[0]) if row and row[0] else ""
        finally:
            conn.close()

    def _upsert_summary(
        self, session_id: str, group_id: str, user_id: str, summary: str, source_turns: int
    ) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO chat_summary
                   (session_id, group_id, user_id, summary, source_turns, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       group_id=excluded.group_id,
                       user_id=excluded.user_id,
                       summary=excluded.summary,
                       source_turns=excluded.source_turns,
                       updated_at=excluded.updated_at""",
                (
                    session_id,
                    group_id,
                    user_id,
                    summary,
                    source_turns,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _upsert_profile(
        self,
        session_id: str,
        group_id: str,
        user_id: str,
        nickname: str,
        identity: str,
        profile: str,
    ) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO user_profile
                   (session_id, group_id, user_id, nickname, identity, profile, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       group_id=excluded.group_id,
                       user_id=excluded.user_id,
                       nickname=excluded.nickname,
                       identity=excluded.identity,
                       profile=excluded.profile,
                       updated_at=excluded.updated_at""",
                (
                    session_id,
                    group_id,
                    user_id,
                    nickname,
                    identity,
                    profile,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_context(self, session_id: str) -> dict[str, str]:
        """返回当前会话的 summary / profile。"""
        return {
            "summary": self.get_summary(session_id),
            "profile": self.get_profile(session_id),
        }

    async def should_reply(
        self,
        session_id: str,
        user_message: str,
        *,
        is_group: bool,
        is_at_me: bool,
    ) -> dict[str, Any]:
        """基于 summary/profile/最近历史判断是否回复。"""
        if is_at_me:
            return {"reply": True, "reason": "群聊中被直接 @，优先回复", "confidence": 1.0}

        group_id, user_id = self._split_session(session_id)
        rel = self._relation.get_or_create_user(user_id)
        summary = self.get_summary(session_id)
        profile = self.get_profile(session_id)
        recent_history = self._history.get_recent_history(session_id, MAX_HISTORY_TURNS)
        history_text = "\n".join(
            f"{item['role']}: {item['content']}" for item in recent_history
        ) or "（暂无）"

        prompt = (
            "你是 YHarvest 的意图识别器，只判断要不要回复，不负责生成回复内容。\n"
            "请结合数据库中的聊天摘要、用户画像和最近聊天记录，判断当前消息是否值得回复。\n\n"
            f"## 会话信息\n"
            f"- session_id: {session_id}\n"
            f"- group_id: {group_id or 'private'}\n"
            f"- user_id: {user_id}\n"
            f"- nickname: {rel['nickname']}\n"
            f"- identity: {rel['identity']}\n"
            f"- is_group: {str(is_group).lower()}\n\n"
            f"## 历史摘要\n{summary or '（暂无）'}\n\n"
            f"## 用户画像\n{profile or '（暂无）'}\n\n"
            f"## 最近聊天\n{history_text}\n\n"
            f"## 当前消息\n{user_message}\n\n"
            "判断规则：\n"
            "- 直接提问、求助、打招呼、情绪表达、延续上下文，优先回复。\n"
            "- 群聊里若明显在和别人聊天、与机器人无关，可以不回复。\n"
            "- 结合画像判断用户是否习惯和机器人对话。\n"
            "- 如果信息不足，宁可回复而不是冷处理。\n\n"
            '返回 JSON：{"reply": true/false, "reason": "一句话原因", "confidence": 0.0~1.0}'
        )

        config = self._intent_llm_config()
        async with self._intent_semaphore:
            try:
                result = await self._chat_json_with_retry(
                    config,
                    [{"role": "user", "content": prompt}],
                    label=f"意图识别 session={session_id}",
                )
            except Exception as exc:
                logger.exception(
                    f"意图识别失败: session={session_id} user_id={user_id} "
                    f"message={user_message!r} error={exc}"
                )
                return {
                    "reply": True,
                    "reason": f"意图识别异常: {type(exc).__name__}",
                    "confidence": 0.5,
                }

        return {
            "reply": bool(result.get("reply", True)),
            "reason": str(result.get("reason", "")),
            "confidence": float(result.get("confidence", 0.5)),
        }

    async def refresh_context(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_message: str = "",
    ) -> None:
        """根据最近聊天记录，更新 summary 和用户画像。"""
        group_id, user_id = self._split_session(session_id)
        rel = self._relation.get_or_create_user(user_id)
        summary = self.get_summary(session_id)
        profile = self.get_profile(session_id)
        recent_history = self._history.get_recent_history(session_id, MAX_HISTORY_TURNS)
        history_text = "\n".join(
            f"{item['role']}: {item['content']}" for item in recent_history
        )

        prompt = (
            "你是 YHarvest 的记忆整理器，要更新数据库中的最近聊天 summary 和用户画像。\n"
            "summary 用于记录最近发生了什么，profile 用于记录相对稳定的用户特征。\n"
            "不要编造不存在的信息，只能根据历史内容总结。\n\n"
            f"## 会话信息\n"
            f"- session_id: {session_id}\n"
            f"- group_id: {group_id or 'private'}\n"
            f"- user_id: {user_id}\n"
            f"- nickname: {rel['nickname']}\n"
            f"- identity: {rel['identity']}\n\n"
            f"## 旧 summary\n{summary or '（暂无）'}\n\n"
            f"## 旧 profile\n{profile or '（暂无）'}\n\n"
            f"## 最近聊天记录\n{history_text or '（暂无）'}\n\n"
            f"## 当前用户消息\n{user_message}\n\n"
            f"## 当前助手回复\n{assistant_message or '（未回复）'}\n\n"
            "请输出 JSON：{\"save\": true/false, \"summary\": \"最近聊天摘要\", \"profile\": \"用户画像\", \"confidence\": 0.0~1.0}\n"
            "要求：summary 2-5 句，profile 2-4 句；profile 只写相对稳定特征，不写一次性细节。"
        )

        if self._profile_semaphore.locked():
            logger.info(f"摘要/画像更新繁忙，跳过: session={session_id}")
            return

        config = self._intent_llm_config()
        async with self._profile_semaphore:
            try:
                result = await self._chat_json_with_retry(
                    config,
                    [{"role": "user", "content": prompt}],
                    label=f"摘要/画像更新 session={session_id}",
                )
            except Exception as exc:
                logger.warning(
                    f"摘要/画像更新失败: session={session_id} "
                    f"user_id={user_id} error={exc}"
                )
                return

        if not result.get("save"):
            return

        summary_text = str(result.get("summary", "")).strip()
        profile_text = str(result.get("profile", "")).strip()
        if not summary_text and not profile_text:
            return

        if summary_text:
            self._upsert_summary(
                session_id,
                group_id,
                user_id,
                summary_text,
                len(recent_history),
            )
        if profile_text:
            self._upsert_profile(
                session_id,
                group_id,
                user_id,
                rel["nickname"],
                rel["identity"],
                profile_text,
            )
