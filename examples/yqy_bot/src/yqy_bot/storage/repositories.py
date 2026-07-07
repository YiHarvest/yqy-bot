from __future__ import annotations

import json
import random
from dataclasses import asdict
from typing import Any

from yqy_bot.core.models import (
    ChatHistoryItem,
    GeneratedResponse,
    GateDecision,
    IntentDecision,
    ParsedMessage,
)
from .database import Database, json_dumps


class Repositories:
    """数据仓库类，提供统一的数据访问接口。"""

    def __init__(self, database: Database) -> None:
        """初始化数据仓库。

        Args:
            database: 数据库连接对象
        """
        self.database = database

    def add_chat_history(
        self,
        *,
        parsed: ParsedMessage,
        role: str,
        content: str,
        source: str = "event",
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> None:
        """添加聊天历史记录到数据库。

        Args:
            parsed: 解析后的消息对象，包含聊天键、范围等信息
            role: 消息角色，"user" 或 "assistant"
            content: 消息文本内容
            metadata: 元数据字典，可选，包含表情、图片等信息
            message_id: 消息 ID，可选，默认使用事件 ID
        """
        self.database.execute(
            """
            INSERT INTO chat_history (
                chat_key, scope_type, scope_id, message_id, role, user_id, content, content_json, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed.chat_key,
                parsed.scope_type,
                parsed.scope_id,
                message_id or parsed.message_id or parsed.event_id,
                role,
                parsed.user_id if role == "user" else parsed.self_id,
                content,
                json_dumps(
                    {
                        **(metadata or {}),
                        "session_id": parsed.session_id,
                        "message_id": parsed.message_id,
                        "message_type": parsed.message_type,
                        "sender_display_name": parsed.sender_display_name,
                        "sender_card": parsed.sender_card,
                        "sender_nickname": parsed.sender_nickname,
                        "self_id": parsed.self_id,
                        "group_id": parsed.group_id,
                        "user_id": parsed.user_id,
                    }
                ),
                source,
            ),
        )

    def recent_history(self, chat_key: str, *, limit: int) -> list[ChatHistoryItem]:
        """获取指定聊天的最近历史记录。

        Args:
            chat_key: 聊天键，格式为 "{scope_type}:{scope_id}"
            limit: 最大记录数量

        Returns:
            聊天历史记录列表，按时间正序排列（旧→新）
        """
        rows = self.database.fetchall(
            """
            SELECT id, chat_key, scope_type, scope_id, message_id, role, user_id, content, content_json, source, created_at
            FROM chat_history
            WHERE chat_key = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_key, limit),
        )
        items = [
            ChatHistoryItem(
                id=int(row["id"]),
                chat_key=str(row["chat_key"]),
                scope_type=str(row["scope_type"]),
                scope_id=str(row["scope_id"]),
                message_id=str(row["message_id"]),
                role=str(row["role"]),
                user_id=str(row["user_id"]),
                content=str(row["content"]),
                created_at=str(row["created_at"]),
                source=str(row["source"]),
                metadata=_loads(row["content_json"]),
            )
            for row in rows
        ]
        return list(reversed(items))

    def find_history_message(self, message_id: str, *, chat_key: str | None = None) -> dict[str, Any] | None:
        """查找指定消息 ID 的历史记录。

        Args:
            message_id: 消息 ID
            chat_key: 聊天键，可选，用于缩小查询范围

        Returns:
            消息历史记录字典，如果不存在则返回 None
        """
        clauses = ["message_id = ?"]
        params: list[Any] = [message_id]
        if chat_key:
            clauses.append("chat_key = ?")
            params.append(chat_key)
        row = self.database.fetchone(
            f"""
            SELECT id, chat_key, scope_type, scope_id, message_id, role, user_id, content, content_json, source, created_at
            FROM chat_history
            WHERE {" AND ".join(clauses)}
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(params),
        )
        return dict(row) if row is not None else None

    def log_intent(self, parsed: ParsedMessage, gate: GateDecision, intent: IntentDecision) -> None:
        """记录意图决策日志到数据库。

        Args:
            parsed: 解析后的消息对象
            gate: 门控决策对象
            intent: 意图决策对象
        """
        self.database.execute(
            """
            INSERT INTO intent_log (
                chat_key, scope_type, scope_id, user_id, decision_json, should_reply,
                reply_style, need_reason_model, need_emoji, group_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed.chat_key,
                parsed.scope_type,
                parsed.scope_id,
                parsed.user_id,
                json_dumps(
                    {
                        "gate": asdict(gate),
                        "intent": asdict(intent),
                    }
                ),
                1 if intent.should_reply else 0,
                intent.reply_style,
                1 if intent.need_reason_model else 0,
                1 if intent.need_emoji else 0,
                gate.group_mode,
            ),
        )

    def get_cooldown_state(self, chat_key: str) -> dict[str, Any] | None:
        """获取指定聊天的冷却状态。

        Args:
            chat_key: 聊天键

        Returns:
            冷却状态字典，包含最后消息时间、最后回复时间、热度等字段，
            如果不存在则返回 None
        """
        row = self.database.fetchone(
            """
            SELECT chat_key, scope_type, scope_id, last_message_at, last_reply_at,
                   recent_10s_count, recent_30s_count, heat_state, last_history_backfill_at
            FROM cooldown_state
            WHERE chat_key = ?
            """,
            (chat_key,),
        )
        if row is None:
            return None
        return dict(row)

    def upsert_cooldown_state(
        self,
        *,
        parsed: ParsedMessage,
        last_message_at: float,
        last_reply_at: float,
        recent_10s_count: int,
        recent_30s_count: int,
        heat_state: str,
        last_history_backfill_at: float = 0.0,
    ) -> None:
        """更新或插入冷却状态记录。

        Args:
            parsed: 解析后的消息对象
            last_message_at: 最后消息时间戳
            last_reply_at: 最后回复时间戳
            recent_10s_count: 最近 10 秒消息数
            recent_30s_count: 最近 30 秒消息数
            heat_state: 群热度状态
        """
        self.database.execute(
            """
            INSERT INTO cooldown_state (
                chat_key, scope_type, scope_id, last_message_at, last_reply_at,
                recent_10s_count, recent_30s_count, heat_state, last_history_backfill_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(chat_key) DO UPDATE SET
                scope_type=excluded.scope_type,
                scope_id=excluded.scope_id,
                last_message_at=excluded.last_message_at,
                last_reply_at=excluded.last_reply_at,
                recent_10s_count=excluded.recent_10s_count,
                recent_30s_count=excluded.recent_30s_count,
                heat_state=excluded.heat_state,
                last_history_backfill_at=excluded.last_history_backfill_at,
                updated_at=datetime('now')
            """,
            (
                parsed.chat_key,
                parsed.scope_type,
                parsed.scope_id,
                last_message_at,
                last_reply_at,
                recent_10s_count,
                recent_30s_count,
                heat_state,
                last_history_backfill_at,
            ),
        )

    def mark_history_backfill(self, parsed: ParsedMessage, *, now: float) -> None:
        """标记历史回填时间，避免频繁回填。

        Args:
            parsed: 解析后的消息对象
            now: 当前时间戳（秒）
        """
        state = self.get_cooldown_state(parsed.chat_key) or {}
        self.upsert_cooldown_state(
            parsed=parsed,
            last_message_at=float(state.get("last_message_at", now)),
            last_reply_at=float(state.get("last_reply_at", 0.0)),
            recent_10s_count=int(state.get("recent_10s_count", 0)),
            recent_30s_count=int(state.get("recent_30s_count", 0)),
            heat_state=str(state.get("heat_state", "quiet")),
            last_history_backfill_at=now,
        )

    def get_user_profile(self, user_id: str) -> dict[str, Any]:
        """获取用户画像数据。

        Args:
            user_id: 用户 ID

        Returns:
            包含 profile（画像字典）和 summary（摘要字符串）的字典
        """
        row = self.database.fetchone(
            "SELECT profile_json, prompt_md, dirty_count FROM user_profile WHERE user_id = ?",
            (user_id,),
        )
        if row is None:
            return {"profile": {}, "prompt_md": "", "dirty_count": 0}
        return {
            "profile": _loads(row["profile_json"]),
            "prompt_md": str(row["prompt_md"]),
            "dirty_count": int(row["dirty_count"] or 0),
        }

    def upsert_user_profile(
        self,
        user_id: str,
        profile: dict[str, Any],
        prompt_md: str,
        *,
        dirty_count: int | None = None,
    ) -> None:
        """更新或插入用户画像记录。

        Args:
            user_id: 用户 ID
            profile: 画像字典，包含用户的兴趣、话题等信息
            prompt_md: 用户画像 Markdown 缓存
            dirty_count: 待刷新的变更计数
        """
        self.database.execute(
            """
            INSERT INTO user_profile (user_id, profile_json, prompt_md, dirty_count, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                profile_json=excluded.profile_json,
                prompt_md=excluded.prompt_md,
                dirty_count=excluded.dirty_count,
                updated_at=datetime('now')
            """,
            (user_id, json_dumps(profile), prompt_md, 0 if dirty_count is None else dirty_count),
        )

    def get_group_profile(self, group_id: str) -> dict[str, Any]:
        """获取群聊画像数据。

        Args:
            group_id: 群号

        Returns:
            包含 profile（画像字典）和 summary（摘要字符串）的字典
        """
        row = self.database.fetchone(
            "SELECT profile_json, prompt_md, message_count_since_update FROM group_profile WHERE group_id = ?",
            (group_id,),
        )
        if row is None:
            return {"profile": {}, "prompt_md": "", "message_count_since_update": 0}
        return {
            "profile": _loads(row["profile_json"]),
            "prompt_md": str(row["prompt_md"]),
            "message_count_since_update": int(row["message_count_since_update"] or 0),
        }

    def upsert_group_profile(
        self,
        group_id: str,
        profile: dict[str, Any],
        prompt_md: str,
        *,
        message_count_since_update: int | None = None,
    ) -> None:
        """更新或插入群聊画像记录。

        Args:
            group_id: 群号
            profile: 画像字典，包含群的话题、活跃度等信息
            prompt_md: 群画像 Markdown 缓存
            message_count_since_update: 上次更新后的消息数
        """
        self.database.execute(
            """
            INSERT INTO group_profile (group_id, profile_json, prompt_md, message_count_since_update, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(group_id) DO UPDATE SET
                profile_json=excluded.profile_json,
                prompt_md=excluded.prompt_md,
                message_count_since_update=excluded.message_count_since_update,
                updated_at=datetime('now')
            """,
            (
                group_id,
                json_dumps(profile),
                prompt_md,
                0 if message_count_since_update is None else message_count_since_update,
            ),
        )

    def bump_group_profile_message_count(self, group_id: str, *, delta: int = 1) -> None:
        """增加群画像的消息计数。

        Args:
            group_id: 号
            delta: 增量值，默认 1
        """
        row = self.database.fetchone(
            "SELECT profile_json, prompt_md, message_count_since_update FROM group_profile WHERE group_id = ?",
            (group_id,),
        )
        if row is None:
            self.upsert_group_profile(group_id, {}, "", message_count_since_update=max(delta, 0))
            return
        self.upsert_group_profile(
            group_id,
            _loads(row["profile_json"]),
            str(row["prompt_md"]),
            message_count_since_update=int(row["message_count_since_update"] or 0) + delta,
        )

    def get_summary(self, chat_key: str) -> str:
        """获取聊天摘要字符串。

        Args:
            chat_key: 聊天键

        Returns:
            聊天摘要字符串，如果不存在则返回空字符串
        """
        row = self.database.fetchone(
            "SELECT summary FROM chat_summary WHERE chat_key = ?",
            (chat_key,),
        )
        return "" if row is None else str(row["summary"])

    def upsert_summary(self, chat_key: str, summary: str) -> None:
        """更新或插入聊天摘要记录。

        Args:
            chat_key: 聊天键
            summary: 聊天摘要字符串
        """
        self.database.execute(
            """
            INSERT INTO chat_summary (chat_key, summary, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(chat_key) DO UPDATE SET
                summary=excluded.summary,
                updated_at=datetime('now')
            """,
            (chat_key, summary),
        )

    def add_memory(
        self,
        *,
        parsed: ParsedMessage,
        kind: str,
        content: str,
        score: float,
        source_message_id: str | None = None,
    ) -> None:
        """添加记忆记录到数据库。

        Args:
            parsed: 解析后的消息对象
            kind: 记忆类型，如 "fact"、"event" 等
            content: 记忆内容文本
            score: 记忆重要性分数，范围 0-1
            source_message_id: 来源消息 ID，可选
        """
        self.database.execute(
            """
            INSERT INTO memory (
                chat_key, scope_type, scope_id, user_id, kind, content, score, source_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed.chat_key,
                parsed.scope_type,
                parsed.scope_id,
                parsed.user_id,
                kind,
                content,
                score,
                source_message_id or parsed.event_id,
            ),
        )

    def add_reflection(self, *, parsed: ParsedMessage, content: str) -> None:
        """添加反思记录到数据库。

        Args:
            parsed: 解析后的消息对象
            content: 反思内容文本
        """
        self.database.execute(
            """
            INSERT INTO reflection (chat_key, scope_type, scope_id, content)
            VALUES (?, ?, ?, ?)
            """,
            (parsed.chat_key, parsed.scope_type, parsed.scope_id, content),
        )

    def add_emoji(
        self,
        *,
        emoji_type: str,
        face_id: str = "",
        emoji_id: str = "",
        emoji_package_id: str = "",
        key: str = "",
        summary: str = "",
        image_url: str = "",
        raw_json: dict[str, Any] | None = None,
    ) -> None:
        """缓存机器人收藏表情到数据库。"""
        existing = self.database.fetchone(
            """
            SELECT usage_count
            FROM emoji_store
            WHERE emoji_type = ?
              AND face_id = ?
              AND emoji_id = ?
              AND emoji_package_id = ?
              AND key = ?
              AND image_url = ?
            """,
            (emoji_type, face_id, emoji_id, emoji_package_id, key, image_url),
        )
        usage_count = 0 if existing is None else int(existing["usage_count"] or 0)
        self.database.execute(
            """
            DELETE FROM emoji_store
            WHERE emoji_type = ?
              AND face_id = ?
              AND emoji_id = ?
              AND emoji_package_id = ?
              AND key = ?
              AND image_url = ?
            """,
            (emoji_type, face_id, emoji_id, emoji_package_id, key, image_url),
        )
        self.database.execute(
            """
            INSERT INTO emoji_store (
                emoji_type, face_id, emoji_id, emoji_package_id, key, summary, image_url, raw_json, usage_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                emoji_type,
                face_id,
                emoji_id,
                emoji_package_id,
                key,
                summary,
                image_url,
                json_dumps(raw_json or {}),
                usage_count,
            ),
        )

    def bump_chat_summary(self, parsed: ParsedMessage, summary: str) -> None:
        """更新聊天摘要（如果摘要非空）。

        Args:
            parsed: 解析后的消息对象
            summary: 新的聊天摘要字符串
        """
        if summary.strip():
            self.upsert_summary(parsed.chat_key, summary)

    def get_recent_memories(
        self,
        *,
        chat_key: str | None = None,
        user_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """获取最近的相关记忆记录。

        Args:
            chat_key: 聊天键，可选
            user_id: 用户 ID，可选
            limit: 最大记录数量，默认 5

        Returns:
            记忆记录列表，按分数和时间排序
        """
        clauses: list[str] = []
        params: list[Any] = []
        if chat_key is not None:
            clauses.append("chat_key = ?")
            params.append(chat_key)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.database.fetchall(
            f"""
            SELECT id, chat_key, scope_type, scope_id, user_id, kind, content, score, source_message_id, created_at
            FROM memory
            {where}
            ORDER BY score DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [dict(row) for row in rows]

    def get_recent_reflections(self, *, chat_key: str | None = None, limit: int = 2) -> list[dict[str, Any]]:
        """获取最近的反思记录。

        Args:
            chat_key: 聊天键，可选
            limit: 最大记录数量，默认 2

        Returns:
            反思记录列表，按时间倒序排列
        """
        clauses: list[str] = []
        params: list[Any] = []
        if chat_key is not None:
            clauses.append("chat_key = ?")
            params.append(chat_key)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.database.fetchall(
            f"""
            SELECT id, chat_key, scope_type, scope_id, content, created_at
            FROM reflection
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [dict(row) for row in rows]

    def list_emojis(self, emoji_type: str | None = None) -> list[dict[str, Any]]:
        """获取缓存表情列表。

        Args:
            emoji_type: 表情类型过滤，可选（face/mface/image）

        Returns:
            表情记录列表
        """
        if emoji_type is None:
            rows = self.database.fetchall(
                """
                SELECT emoji_type, face_id, emoji_id, emoji_package_id, key, summary, image_url, raw_json, usage_count
                FROM emoji_store
                """
            )
        else:
            rows = self.database.fetchall(
                """
                SELECT emoji_type, face_id, emoji_id, emoji_package_id, key, summary, image_url, raw_json, usage_count
                FROM emoji_store
                WHERE emoji_type = ?
                """,
                (emoji_type,),
            )
        return [dict(row) for row in rows]

    def random_emoji(self, emoji_type: str | None = None) -> dict[str, Any] | None:
        """获取随机表情。

        Args:
            emoji_type: 表情类型过滤，可选

        Returns:
            随机表情记录，如果缓存为空则返回 None
        """
        items = self.list_emojis(emoji_type)
        if not items:
            return None
        return random.choice(items)

    def increment_emoji_usage(
        self,
        *,
        emoji_type: str,
        face_id: str = "",
        emoji_id: str = "",
        emoji_package_id: str = "",
        key: str = "",
        image_url: str = "",
    ) -> None:
        """增加表情使用计数。

        Args:
            emoji_type: 表情类型
            face_id: 表情 ID（内置表情）
            emoji_id: 小表情 ID
            emoji_package_id: 表情包 ID
            key: 表情键
            image_url: 图片 URL
        """
        self.database.execute(
            """
            UPDATE emoji_store
            SET usage_count = COALESCE(usage_count, 0) + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE emoji_type = ?
              AND face_id = ?
              AND emoji_id = ?
              AND emoji_package_id = ?
              AND key = ?
              AND image_url = ?
            """,
            (emoji_type, face_id, emoji_id, emoji_package_id, key, image_url),
        )


def _loads(payload: str) -> dict[str, Any]:
    """解析 JSON 字符串为字典，失败时返回空字典。

    Args:
        payload: JSON 字符串

    Returns:
        解析后的字典，如果解析失败或内容不是对象则返回空字典
    """
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
