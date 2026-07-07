from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from yqy_bot.core.config import ProjectConfig
from yqy_bot.core.models import ChatHistoryItem, ConversationContext, IntentDecision, ParsedMessage
from yqy_bot.qq.napcat_tools import NapCatTools
from yqy_bot.qq.parser import parse_message_input
from yqy_bot.storage.repositories import Repositories

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ContextBuilder:
    """对话上下文构建器，负责组装完整的对话上下文数据。"""

    config: ProjectConfig
    repos: Repositories
    napcat_tools: NapCatTools

    async def build(self, parsed: ParsedMessage, intent: IntentDecision, *, group_heat_state: str) -> ConversationContext:
        """构建完整的对话上下文对象。

        从数据库和 NapCat API 加载历史、画像、记忆等数据，
        组装为结构化的 ConversationContext 对象。

        Args:
            parsed: 解析后的消息对象
            intent: 意图决策对象
            group_heat_state: 群热度状态

        Returns:
            完整的对话上下文对象，包含所有必要信息
        """
        bot = self.config.bot
        start_time = time.time()
        # 并行加载基础数据
        recent_history_task = asyncio.to_thread(self._load_recent_history, parsed)
        chat_summary_task = asyncio.to_thread(self.repos.get_summary, parsed.chat_key)
        user_state_task = asyncio.to_thread(self.repos.get_user_profile, parsed.user_id)
        if parsed.is_group:
            group_state_task = asyncio.to_thread(self.repos.get_group_profile, parsed.group_id)
        else:
            group_state_task = asyncio.sleep(0, result={"profile": {}, "prompt_md": "", "message_count_since_update": 0})
        memories_task = asyncio.to_thread(self._load_memories, parsed)
        reflections_task = asyncio.to_thread(self._load_reflections, parsed)
        # 等待基础数据加载完成
        recent_history, chat_summary, user_state, group_state, memories_raw, reflections_raw = await asyncio.gather(
            recent_history_task,
            chat_summary_task,
            user_state_task,
            group_state_task,
            memories_task,
            reflections_task,
        )
        # 处理用户画像和群画像（纯 CPU 操作）
        user_profile = dict(user_state.get("profile", {}))
        group_profile = dict(group_state.get("profile", {}))
        user_profile_md = _profile_md(
            user_state.get("prompt_md", ""),
            user_profile,
            title="用户画像",
            current_text=parsed.text,
        )
        group_profile_md = _profile_md(group_state.get("prompt_md", ""), group_profile, title="群画像")
        # 异步加载引用消息和回填历史
        reference_message_task = self._load_reference_message(parsed)
        backfill_task = (
            self._maybe_backfill_group_history(parsed, recent_history)
            if parsed.is_group
            else asyncio.sleep(0, result=None)
        )
        reference_message, _ = await asyncio.gather(reference_message_task, backfill_task)
        # 如果回填了历史，重新加载
        if parsed.is_group:
            recent_history = self._load_recent_history(parsed)
        # 过滤冲突记忆和处理数据
        relevant_memories = self._filter_conflicting_memories(parsed, memories_raw)
        reflections = reflections_raw
        recent_turns = self._render_recent_turns(recent_history, parsed)
        LOGGER.info(
            "[上下文构建] session_id=%s elapsed=%.2fs recent_turns=%s memories=%s reflections=%s has_user_profile=%s has_group_profile=%s",
            parsed.session_id,
            time.time() - start_time,
            len(recent_turns),
            len(relevant_memories),
            len(reflections),
            bool(user_profile_md.strip()),
            bool(group_profile_md.strip()),
        )
        current_message = {
            "text": parsed.text,
            "user_id": parsed.user_id,
            "group_id": parsed.group_id,
            "session_id": parsed.session_id,
            "is_group": parsed.is_group,
            "is_private": parsed.is_private,
            "is_at_bot": parsed.is_at_bot,
            "reply_message_id": parsed.reply_message_id,
            "sender_display_name": parsed.sender_display_name,
            "message_id": parsed.message_id,
        }
        context_stats = {
            "recent_turns_count": len(recent_turns),
            "memory_count": len(relevant_memories),
            "has_user_profile": bool(user_profile_md.strip()),
            "has_group_profile": bool(group_profile_md.strip()),
        }
        context_data = {
            "current_message": current_message,
            "recent_turns": recent_turns,
            "chat_summary": chat_summary,
            "user_profile_md": user_profile_md,
            "group_profile_md": group_profile_md,
            "relevant_memories": relevant_memories,
            "memory_conflict_policy": "current_message_overrides_prior_memory",
            "reflections": reflections,
            "intent_decision": {
                "should_reply": intent.should_reply,
                "reply_style": intent.reply_style,
                "need_reason_model": intent.need_reason_model,
                "need_emoji": intent.need_emoji,
                "confidence": intent.confidence,
                "reply_length": intent.reply_length,
                "notes": intent.notes,
            },
            "group_heat_state": group_heat_state,
            "context_stats": context_stats,
            "summary_policy": "old_facts_only",
            "runtime_state": {
                "group_heat_state": group_heat_state,
                "is_at_bot": parsed.is_at_bot,
                "reply_message_id": parsed.reply_message_id,
                "intent_should_reply": intent.should_reply,
                "memory_conflict_policy": "current_message_overrides_prior_memory",
                "summary_policy": "old_facts_only",
            },
            "reference_message": reference_message,
        }
        extra_notes = []
        if intent.reply_style:
            extra_notes.append(f"回复风格：{intent.reply_style}")
        if intent.need_reason_model:
            extra_notes.append("需要更稳一点的推理表达")
        return ConversationContext(
            parsed=parsed,
            recent_history=recent_history,
            summary=chat_summary,
            user_profile=user_profile,
            group_profile=group_profile,
            long_context="",
            persona=self.config.persona,
            safety=self.config.safety,
            bot=bot,
            extra_notes=extra_notes,
            context_data=context_data,
            prompt_md="",
            user_profile_md=user_profile_md,
            group_profile_md=group_profile_md,
            relevant_memories=relevant_memories,
            reflections=reflections,
            intent_decision=context_data["intent_decision"],
            group_heat_state=group_heat_state,
            context_stats=context_stats,
        )

    def _load_recent_history(self, parsed: ParsedMessage) -> list[ChatHistoryItem]:
        """加载最近聊天历史记录。

        Args:
            parsed: 解析后的消息对象

        Returns:
            聊天历史记录列表，群聊和私聊使用不同的限制
        """
        if parsed.is_group:
            limit = self.config.bot.context.group_recent_turns_limit
        else:
            limit = self.config.bot.context.private_recent_turns_limit
        return self.repos.recent_history(parsed.chat_key, limit=limit)

    async def _load_reference_message(self, parsed: ParsedMessage) -> dict[str, Any] | None:
        """加载引用消息的详细信息。

        优先从本地数据库查询，如果未找到且启用了历史回填，
        则从 NapCat API 获取并缓存。

        Args:
            parsed: 解析后的消息对象

        Returns:
            引用消息字典，如果不存在则返回 None
        """
        if not parsed.reply_message_id:
            return None
        local = self.repos.find_history_message(parsed.reply_message_id, chat_key=parsed.chat_key)
        if local is not None:
            return _reference_message_from_row(local)
        if not self.config.bot.context.enable_history_backfill:
            return None
        try:
            payload = await self.napcat_tools.get_msg(parsed.reply_message_id)
        except Exception:
            return None
        raw_event = _extract_raw_event(payload)
        if not raw_event:
            return None
        referenced = parse_message_input({"event_id": parsed.reply_message_id, "adapter": parsed.adapter, "platform": parsed.platform, "raw_event": raw_event})
        self.repos.add_chat_history(
            parsed=referenced,
            role="user",
            content=referenced.text,
            source="napcat_history",
            metadata={"segments": referenced.segments},
            message_id=referenced.message_id or parsed.reply_message_id,
        )
        row = self.repos.find_history_message(parsed.reply_message_id, chat_key=parsed.chat_key)
        return _reference_message_from_row(row) if row is not None else raw_event

    async def _maybe_backfill_group_history(self, parsed: ParsedMessage, recent_history: list[ChatHistoryItem]) -> None:
        """必要时回填群聊历史消息。

        当群聊历史记录不足且满足回填冷却条件时，
        从 NapCat API 获取并缓存历史消息。

        Args:
            parsed: 解析后的消息对象
            recent_history: 当前已加载的历史记录
        """
        ctx = self.config.bot.context
        if not parsed.is_group or not ctx.enable_history_backfill:
            return
        if len(recent_history) >= ctx.group_recent_turns_min:
            return
        state = self.repos.get_cooldown_state(parsed.chat_key) or {}
        now = time.time()
        last_backfill = float(state.get("last_history_backfill_at", 0.0))
        if now - last_backfill < ctx.history_backfill_cooldown_seconds:
            return
        try:
            payload = await self.napcat_tools.get_group_msg_history(
                parsed.group_id,
                message_seq=0,
                count=ctx.group_recent_turns_limit,
            )
        except Exception:
            return
        messages = _extract_history_messages(payload)
        seen = {item.message_id for item in recent_history if item.message_id}
        for raw_message in messages:
            normalized = parse_message_input({"event_id": raw_message.get("message_id", ""), "adapter": parsed.adapter, "platform": parsed.platform, "raw_event": raw_message})
            if not normalized.message_id or normalized.message_id in seen:
                continue
            if self.repos.find_history_message(normalized.message_id, chat_key=parsed.chat_key) is not None:
                continue
            seen.add(normalized.message_id)
            self.repos.add_chat_history(
                parsed=normalized,
                role="user",
                content=normalized.text,
                source="napcat_history",
                metadata={"segments": normalized.segments},
                message_id=normalized.message_id,
            )
        self.repos.mark_history_backfill(parsed, now=now)

    def _load_memories(self, parsed: ParsedMessage) -> list[dict[str, Any]]:
        """加载相关记忆记录。

        从群聊和用户维度获取记忆，去重并按分数过滤。

        Args:
            parsed: 解析后的消息对象

        Returns:
            记忆记录列表，已去重和过滤
        """
        rows: list[dict[str, Any]] = []
        if parsed.is_group:
            rows.extend(self.repos.get_recent_memories(chat_key=parsed.chat_key, limit=3))
        rows.extend(self.repos.get_recent_memories(user_id=parsed.user_id, limit=5))
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if float(row.get("score", 0.0) or 0.0) < 0.35:
                continue
            key = f"{row.get('kind','')}:{row.get('content','')}:{row.get('source_message_id','')}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique[: self.config.bot.context.memory_limit]

    def _load_reflections(self, parsed: ParsedMessage) -> list[dict[str, Any]]:
        """加载反思记录。

        Args:
            parsed: 解析后的消息对象

        Returns:
            反思记录列表
        """
        rows = self.repos.get_recent_reflections(chat_key=parsed.chat_key, limit=2)
        if parsed.is_private:
            rows.extend(self.repos.get_recent_reflections(chat_key=parsed.user_id, limit=1))
        return rows[: self.config.bot.context.reflection_limit]

    def _filter_conflicting_memories(self, parsed: ParsedMessage, memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """过滤与当前消息冲突的记忆。

        当当前消息表达否定或变更意图时，
        过滤掉与其冲突的旧记忆。

        Args:
            parsed: 解析后的消息对象
            memories: 原始记忆列表

        Returns:
            过滤后的记忆列表
        """
        text = parsed.text.strip()
        if not text or not _looks_like_conflict_message(text):
            return memories
        filtered: list[dict[str, Any]] = []
        dropped = 0
        for memory in memories:
            content = str(memory.get("content", ""))
            if _memory_conflicts_with_current(text, content):
                dropped += 1
                continue
            filtered.append(memory)
        if dropped:
            LOGGER.debug(
                "memory conflict filter session_id=%s dropped=%s kept=%s",
                parsed.session_id,
                dropped,
                len(filtered),
            )
        return filtered

    def _render_recent_turns(self, recent_history: list[ChatHistoryItem], parsed: ParsedMessage) -> list[dict[str, Any]]:
        """将聊天历史转换为对话轮次格式。

        Args:
            recent_history: 聊天历史记录列表
            parsed: 解析后的消息对象

        Returns:
            对话轮次列表，每个元素包含角色、内容、发送者等信息
        """
        turns: list[dict[str, Any]] = []
        for item in recent_history:
            display_name = str(item.metadata.get("sender_display_name") or item.user_id or "").strip()
            if not display_name:
                display_name = item.user_id
            turns.append(
                {
                    "role": item.role,
                    "content": item.content,
                    "user_id": item.user_id,
                    "sender_display_name": display_name,
                    "message_id": item.metadata.get("message_id", ""),
                    "session_id": item.chat_key,
                    "is_current_session": item.chat_key == parsed.chat_key,
                    "source": item.source,
                }
            )
        return turns


def _profile_md(prompt_md: str, profile_json: dict[str, Any], *, title: str, current_text: str = "") -> str:
    """生成画像 Markdown 文本。

    如果已有缓存文本则直接使用，否则从画像字典生成。

    Args:
        prompt_md: 缓存的 Markdown 文本
        profile_json: 画像字典
        title: 标题文本

    Returns:
        Markdown 格式的画像文本
    """
    if prompt_md.strip():
        rendered = _filter_profile_md_lines(prompt_md.strip().splitlines(), title=title, current_text=current_text)
        if rendered:
            return rendered
    if not profile_json:
        return ""
    lines = [f"### {title}"]
    for key, value in profile_json.items():
        if isinstance(value, list):
            items = [str(item) for item in value if not _profile_item_conflicts_with_current(current_text, str(item))]
            if not items:
                continue
            rendered = ", ".join(items)
        elif isinstance(value, dict):
            rendered = ", ".join(f"{k}={v}" for k, v in value.items())
        else:
            rendered = str(value)
        if _profile_item_conflicts_with_current(current_text, rendered):
            continue
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


def _filter_profile_md_lines(lines: list[str], *, title: str, current_text: str) -> str:
    filtered = [line.strip() for line in lines if line.strip()]
    if current_text.strip():
        filtered = [line for line in filtered if not _profile_item_conflicts_with_current(current_text, line)]
    if not filtered:
        return ""
    if filtered[0] != f"### {title}":
        filtered.insert(0, f"### {title}")
    return "\n".join(filtered)


def _profile_item_conflicts_with_current(current_text: str, item_text: str) -> bool:
    current_text = current_text.strip()
    item_text = item_text.strip()
    if not current_text or not item_text:
        return False
    if not _looks_like_conflict_message(current_text):
        return False
    subjects = _extract_subject_tokens(current_text)
    if not subjects:
        return False
    if any(token in current_text for token in ["不喜欢", "不再", "不是"]):
        return any(subject in item_text and ("喜欢" in item_text or "偏好" in item_text) for subject in subjects)
    if any(token in current_text for token in ["改成", "改为", "取消", "别记", "别把", "改口"]):
        return any(subject in item_text for subject in subjects)
    return False


def _extract_raw_event(payload: dict[str, Any]) -> dict[str, Any]:
    """从 API 响应中提取原始事件字典。

    尝试从多个可能的字段位置获取原始事件数据。

    Args:
        payload: API 响应载荷

    Returns:
        原始事件字典，如果未找到则返回空字典
    """
    if isinstance(payload.get("raw_event"), dict):
        return payload["raw_event"]
    if {"self_id", "user_id", "message_id", "message_type", "message"}.issubset(payload.keys()):
        return payload
    if isinstance(payload.get("data"), dict):
        data = payload["data"]
        if {"self_id", "user_id", "message_id", "message_type", "message"}.issubset(data.keys()):
            return data
        if isinstance(data.get("message"), dict):
            return data["message"]
        if isinstance(data.get("message"), list):
            return {"message": data["message"]}
    return {}


def _extract_history_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从历史消息 API 响应中提取消息列表。

    Args:
        payload: API 响应载荷

    Returns:
        消息字典列表
    """
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("messages", "list", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _looks_like_conflict_message(text: str) -> bool:
    """判断文本是否包含冲突表达。

    Args:
        text: 消息文本内容

    Returns:
        如果包含否定或变更关键词则返回 True
    """
    return any(token in text for token in ["不喜欢", "不再", "改成", "改为", "别记", "别把", "不是", "取消", "改口", "相反"])


def _memory_conflicts_with_current(current_text: str, memory_text: str) -> bool:
    """判断记忆是否与当前消息冲突。

    Args:
        current_text: 当前消息文本
        memory_text: 记忆内容文本

    Returns:
        如果存在冲突则返回 True
    """
    if not memory_text:
        return False
    subjects = _extract_subject_tokens(current_text)
    if not subjects:
        return False
    if any(token in current_text for token in ["不喜欢", "不再", "不是"]):
        return any(subject in memory_text and "喜欢" in memory_text for subject in subjects)
    if any(token in current_text for token in ["改成", "改为", "取消", "别记", "别把", "改口"]):
        return any(subject in memory_text for subject in subjects)
    return False


def _extract_subject_tokens(text: str) -> list[str]:
    """从文本中提取主体词汇。

    使用正则表达式从否定或变更表达中提取主体对象。

    Args:
        text: 消息文本内容

    Returns:
        主体词汇列表，最大长度 24 字符
    """
    patterns = [
        r"不喜欢\s*([^\s，。！？,;；]{1,24})",
        r"不再\s*([^\s，。！？,;；]{1,24})",
        r"喜欢\s*([^\s，。！？,;；]{1,24})",
        r"改成\s*([^\s，。！？,;；]{1,24})",
        r"改为\s*([^\s，。！？,;；]{1,24})",
    ]
    tokens: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            token = match.group(1).strip()
            if token:
                tokens.append(token[:24])
    return list(dict.fromkeys(tokens))


def _reference_message_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """从数据库行构建引用消息字典。

    Args:
        row: 数据库行字典

    Returns:
        引用消息字典，包含消息 ID、内容、元数据等字段
    """
    content_json = row.get("content_json", {})
    metadata: dict[str, Any]
    if isinstance(content_json, str):
        try:
            import json

            parsed = json.loads(content_json)
        except Exception:
            parsed = {}
        metadata = parsed if isinstance(parsed, dict) else {}
    elif isinstance(content_json, dict):
        metadata = content_json
    else:
        metadata = {}
    return {
        "message_id": row.get("message_id", ""),
        "chat_key": row.get("chat_key", ""),
        "role": row.get("role", ""),
        "user_id": row.get("user_id", ""),
        "content": row.get("content", ""),
        "source": row.get("source", ""),
        "metadata": metadata,
    }
