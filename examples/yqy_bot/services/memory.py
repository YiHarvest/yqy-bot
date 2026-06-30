"""长期记忆服务，持久化用户的重要事实。"""

from __future__ import annotations

from .db import get_connection


class MemoryService:
    """长期记忆读写服务。"""

    def save_memory(self, user_id: str, content: str, importance: float = 0.5) -> None:
        """保存一条用户记忆。"""
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO memory (user_id, content, importance) VALUES (?, ?, ?)",
                (user_id, content, importance),
            )
            conn.commit()
        finally:
            conn.close()

    def get_memories(self, user_id: str, limit: int = 20) -> list[str]:
        """返回该用户的记忆内容列表（按重要性降序）。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT content FROM memory
                WHERE user_id = ?
                ORDER BY importance DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()
