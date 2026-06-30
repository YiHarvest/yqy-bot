"""社会记忆网络：记录用户提到的人物关系，建立跨用户记忆图谱。"""

from __future__ import annotations

from typing import Any

from .db import get_connection


class SocialMemoryService:
    """社会记忆读写服务。"""

    def save_social_memory(
        self,
        subject_user: str,
        target_user: str,
        relation: str,
        content: str,
        importance: float = 0.5,
    ) -> None:
        """保存一条社会关系记忆。

        Args:
            subject_user: 消息发送者的 user_id
            target_user: 被提到的人的标识（昵称或 user_id）
            relation: 关系类型（关心/观察/吐槽/帮助等）
            content: 关系内容描述
            importance: 重要度 0~1
        """
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO social_memory
                   (subject_user, target_user, relation, content, importance)
                   VALUES (?, ?, ?, ?, ?)""",
                (subject_user, target_user, relation, content, importance),
            )
            conn.commit()
        finally:
            conn.close()

    def get_related_memories(
        self, user_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """获取与该用户相关的所有社会记忆（作为主体或被提及对象）。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT subject_user, target_user, relation, content, importance
                   FROM social_memory
                   WHERE subject_user = ? OR target_user = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (user_id, user_id, limit),
            ).fetchall()
            rows_reversed = list(rows)
            rows_reversed.reverse()
            return [
                {
                    "subject_user": row[0],
                    "target_user": row[1],
                    "relation": row[2],
                    "content": row[3],
                    "importance": row[4],
                }
                for row in rows_reversed
            ]
        finally:
            conn.close()

    def describe_user(self, user_id: str) -> str:
        """返回该用户相关的社会关系描述，用于注入 Prompt。"""
        mems = self.get_related_memories(user_id)
        if not mems:
            return ""

        lines: list[str] = []
        for mem in mems:
            if mem["subject_user"] == user_id:
                lines.append(
                    f"- {user_id} 对 {mem['target_user']}（{mem['relation']}）：{mem['content']}"
                )
            else:
                lines.append(
                    f"- {mem['subject_user']} 对 {user_id}（{mem['relation']}）：{mem['content']}"
                )
        return "\n".join(lines)
