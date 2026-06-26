"""关系系统：每位用户的好感度、亲密度、信任度持久化与调整。
所有参数从 config/relation.json 加载。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import get_connection

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "relation.json"
_USERS_PATH = Path(__file__).resolve().parents[1] / "config" / "users.json"


def _load_json(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


_cfg = _load_json(_CONFIG_PATH)

RELATION_POSITIVE: frozenset[str] = frozenset(_cfg.get("positive_keywords", []))
RELATION_NEGATIVE: frozenset[str] = frozenset(_cfg.get("negative_keywords", []))

POSITIVE_FAVORABILITY_DELTA: int = int(_cfg.get("positive_favorability_delta", 2))
NEGATIVE_FAVORABILITY_DELTA: int = int(_cfg.get("negative_favorability_delta", -5))
REPLY_INTIMACY_DELTA: int = int(_cfg.get("reply_intimacy_delta", 1))


def _load_users() -> dict[str, dict[str, str]]:
    return _load_json(_USERS_PATH)


class RelationshipService:
    """用户关系管理服务。"""

    @staticmethod
    def _clamp(value: int) -> int:
        return max(0, min(100, value))

    def get_or_create_user(self, user_id: str) -> dict[str, Any]:
        """获取或创建用户关系记录。"""
        users = _load_users()
        profile = users.get(user_id, {})
        nickname = profile.get("nickname", user_id)
        identity = profile.get("identity", "好友")

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT nickname, identity, favorability, intimacy, trust FROM relationship WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO relationship (user_id, nickname, identity)
                       VALUES (?, ?, ?)""",
                    (user_id, nickname, identity),
                )
                conn.commit()
                return {
                    "user_id": user_id,
                    "nickname": nickname,
                    "identity": identity,
                    "favorability": 50,
                    "intimacy": 50,
                    "trust": 50,
                }
            if row[0] != nickname or row[1] != identity:
                conn.execute(
                    "UPDATE relationship SET nickname = ?, identity = ? WHERE user_id = ?",
                    (nickname, identity, user_id),
                )
                conn.commit()
            return {
                "user_id": user_id,
                "nickname": nickname,
                "identity": identity,
                "favorability": row[2],
                "intimacy": row[3],
                "trust": row[4],
            }
        finally:
            conn.close()

    def update_last_chat_time(self, user_id: str) -> None:
        """更新用户的最后聊天时间。"""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE relationship SET last_chat_time = ? WHERE user_id = ?",
                (datetime.now(timezone.utc).isoformat(), user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _adjust(self, user_id: str, field: str, delta: int) -> None:
        conn = get_connection()
        try:
            conn.execute(
                f"UPDATE relationship SET {field} = MIN(100, MAX(0, {field} + ?)) WHERE user_id = ?",
                (delta, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def adjust_favorability(self, user_id: str, delta: int) -> None:
        """调整好感度，限制 0~100。"""
        self._adjust(user_id, "favorability", delta)

    def adjust_intimacy(self, user_id: str, delta: int) -> None:
        """调整亲密度，限制 0~100。"""
        self._adjust(user_id, "intimacy", delta)

    def adjust_trust(self, user_id: str, delta: int) -> None:
        """调整信任度，限制 0~100。"""
        self._adjust(user_id, "trust", delta)

    def describe(self, user_id: str) -> str:
        """返回关系上下文，用于注入 Prompt。"""
        rel = self.get_or_create_user(user_id)
        lines = [
            "当前聊天对象：",
            f"昵称：{rel['nickname']}",
            f"身份：{rel['identity']}",
            f"好感度：{rel['favorability']}",
            f"熟悉度：{rel['intimacy']}",
            f"信任度：{rel['trust']}",
        ]
        identity = rel["identity"]
        if identity == "yqy":
            lines.append("关系：熟人，可以吐槽、玩梗、阴阳怪气。")
        elif identity == "妹妹":
            lines.append("关系：平时可以温和，但她喜欢捉弄你，你是大王。她捉弄你时直接毒舌反击，不用惯着。")
        return "\n".join(lines)

    def get_all_users(self) -> list[dict[str, Any]]:
        """返回所有用户，按好感度降序排列。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT user_id, nickname, identity, favorability, intimacy, trust
                   FROM relationship
                   ORDER BY favorability DESC"""
            ).fetchall()
            return [
                {
                    "user_id": row[0],
                    "nickname": row[1],
                    "identity": row[2],
                    "favorability": row[3],
                    "intimacy": row[4],
                    "trust": row[5],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def last_active_action(self, user_id: str) -> str | None:
        """返回该用户最近一次主动行为的创建时间（ISO 字符串），无记录则返回 None。"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT created_at FROM behavior_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
