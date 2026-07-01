"""聊天历史服务，封装 SQLite 中的聊天记录增删查操作。"""

from __future__ import annotations

from .db import get_connection


class HistoryService:
    """聊天历史持久化服务。"""

    def get_recent_history(
        self, session_id: str, max_turns: int
    ) -> list[dict[str, str]]:
        """返回最近 N 轮对话（N*2 条消息），兼容原有格式。

        返回格式：
        [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT role, content
                FROM chat_history
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, max_turns * 2),
            ).fetchall()
            # SQL 返回按 id DESC，翻转回时间正序
            rows.reverse()
            return [{"role": row[0], "content": row[1]} for row in rows]
        finally:
            conn.close()

    def get_recent_history_for_user(
        self, user_id: str, max_turns: int
    ) -> list[dict[str, str]]:
        """返回某个用户最近 N 轮相关对话。

        这里按 ``user_id`` 聚合，适用于主动聊天或用户级画像场景；
        与 ``get_recent_history()`` 的 session 级查询语义分开，避免把
        ``user_id`` 误当作 ``session_id`` 使用。
        """
        if not user_id:
            return []

        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT role, content
                FROM chat_history
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, max_turns * 2),
            ).fetchall()
            rows.reverse()
            return [{"role": row[0], "content": row[1]} for row in rows]
        finally:
            conn.close()

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        group_id: str = "",
        user_id: str = "",
    ) -> None:
        """保存一条消息。"""
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO chat_history
                   (session_id, group_id, user_id, role, content)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, group_id, user_id, role, content),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_user_messages(self, session_id: str, limit: int = 20) -> list[str]:
        """返回最近 N 条 user 消息的纯文本列表（不含 assistant 消息）。

        用于构建事实闸门的 evidence_text，只取 user 角色。
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT content FROM chat_history
                WHERE session_id = ? AND role = 'user'
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
            rows_reversed = list(rows)
            rows_reversed.reverse()
            return [row[0] for row in rows_reversed]
        finally:
            conn.close()

    def append_turn(
        self,
        session_id: str,
        user_msg: str,
        ai_msg: str,
        *,
        group_id: str = "",
        user_id: str = "",
    ) -> None:
        """一次性保存用户消息和 AI 消息（在同一事务中）。"""
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO chat_history
                   (session_id, group_id, user_id, role, content)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, group_id, user_id, "user", user_msg),
            )
            conn.execute(
                """INSERT INTO chat_history
                   (session_id, group_id, user_id, role, content)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, group_id, user_id, "assistant", ai_msg),
            )
            conn.commit()
        finally:
            conn.close()
