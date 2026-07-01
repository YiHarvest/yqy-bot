"""反思系统：让 YHarvest 从聊天中提炼自己的观察和判断。
配置已合并到 config_service.py。
"""

from __future__ import annotations

from .config_service import MAX_RECENT_REFLECTIONS
from .db import get_connection


class ReflectionService:
    """反思存储与读取服务。"""

    def count_today(self, user_id: str) -> int:
        """返回某个用户今天已生成的反思数量。"""
        if not user_id:
            return 0

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM reflection
                WHERE user_id = ?
                  AND date(created_at) = date('now', 'localtime')
                """,
                (user_id,),
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def save_reflection(
        self,
        user_id: str,
        content: str,
        importance: float = 0.5,
        *,
        session_id: str = "",
    ) -> None:
        """保存一条反思，并记录来源用户/会话。"""
        if not user_id:
            return

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO reflection (user_id, session_id, content, importance)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, session_id, content, importance),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent(self, user_id: str, limit: int | None = None) -> list[str]:
        """返回某个用户最近 N 条反思内容。"""
        if not user_id:
            return []

        n = limit if limit is not None else MAX_RECENT_REFLECTIONS
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT content
                FROM reflection
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, n),
            ).fetchall()
            rows.reverse()
            return [row[0] for row in rows]
        finally:
            conn.close()
