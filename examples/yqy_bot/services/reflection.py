"""反思系统：让 YHarvest 从聊天中提炼自己的观察和判断。
所有参数从 config/reflection.json 加载。
"""

from __future__ import annotations

import json
from pathlib import Path

from .db import get_connection

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "reflection.json"


def _load() -> dict:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


_cfg = _load()

MAX_REFLECTIONS_PER_DAY: int = int(_cfg.get("max_reflections_per_day", 3))
MAX_RECENT_REFLECTIONS: int = int(_cfg.get("max_recent_reflections", 10))


class ReflectionService:
    """反思存储与读取服务。"""

    def count_today(self) -> int:
        """返回今天已生成的反思数量。"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM reflection WHERE date(created_at) = date('now', 'localtime')"
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def save_reflection(self, content: str, importance: float = 0.5) -> None:
        """保存一条反思。"""
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO reflection (content, importance) VALUES (?, ?)",
                (content, importance),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent(self, limit: int | None = None) -> list[str]:
        """返回最近 N 条反思内容。"""
        n = limit if limit is not None else MAX_RECENT_REFLECTIONS
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT content FROM reflection ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
            rows.reverse()
            return [row[0] for row in rows]
        finally:
            conn.close()
