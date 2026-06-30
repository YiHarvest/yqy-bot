"""星星人设情绪系统：状态持久化、时间衰减、事件调整。
所有参数从 config/mood.json 加载。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .config_service import get_mood_config
from .db import get_connection

_cfg = get_mood_config()

POSITIVE_KEYWORDS: frozenset[str] = frozenset(_cfg.get("positive_keywords", []))
NEGATIVE_KEYWORDS: frozenset[str] = frozenset(_cfg.get("negative_keywords", []))

_tick = _cfg.get("tick", {})
_reaction = _cfg.get("message_reaction", {})
_reply_cost = _cfg.get("reply_cost", {})

TICK_ENERGY_DECAY_SECONDS: int = int(_tick.get("energy_decay_seconds", 3600))
TICK_ENERGY_DECAY_DELTA: int = int(_tick.get("energy_decay_delta", -1))
TICK_LONELINESS_12H_SECONDS: int = int(_tick.get("loneliness_rise_12h_seconds", 43200))
TICK_LONELINESS_12H_DELTA: int = int(_tick.get("loneliness_rise_12h_delta", 2))
TICK_LONELINESS_24H_SECONDS: int = int(_tick.get("loneliness_rise_24h_seconds", 86400))
TICK_LONELINESS_24H_DELTA: int = int(_tick.get("loneliness_rise_24h_delta", 3))

REACTION_LONELINESS_DELTA: int = int(_reaction.get("loneliness_delta", -3))
REACTION_POSITIVE_MOOD_DELTA: int = int(_reaction.get("positive_mood_delta", 3))
REACTION_NEGATIVE_MOOD_DELTA: int = int(_reaction.get("negative_mood_delta", -8))

REPLY_ENERGY_DELTA: int = int(_reply_cost.get("energy_delta", -1))


def _parse_labels(raw: dict[str, str]) -> dict[range, str]:
    """将 "0-20": "label" 格式转为 range → label 映射。"""
    result: dict[range, str] = {}
    for key, label in raw.items():
        lo, _, hi = key.partition("-")
        result[range(int(lo), int(hi) + 1)] = label
    return result


MOOD_LABELS: dict[range, str] = _parse_labels(_cfg.get("mood_labels", {}))
ENERGY_LABELS: dict[range, str] = _parse_labels(_cfg.get("energy_labels", {}))
LONELINESS_LABELS: dict[range, str] = _parse_labels(_cfg.get("loneliness_labels", {}))


def _label(value: int, labels: dict[range, str]) -> str:
    for rng, text in labels.items():
        if value in rng:
            return text
    return str(value)


class MoodService:
    """星星的情绪状态服务，所有用户共享同一个星星人格。"""

    @staticmethod
    def _clamp(value: int) -> int:
        return max(0, min(100, value))

    def get_state(self) -> dict[str, int]:
        """返回当前情绪状态快照。"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT mood, energy, loneliness FROM persona_state WHERE id = 1"
            ).fetchone()
            if row is None:
                return {"mood": 50, "energy": 80, "loneliness": 20}
            return {"mood": row[0], "energy": row[1], "loneliness": row[2]}
        finally:
            conn.close()

    def _update(
        self, mood_delta: int, energy_delta: int, loneliness_delta: int
    ) -> None:
        conn = get_connection()
        try:
            state = self.get_state()
            new_mood = self._clamp(state["mood"] + mood_delta)
            new_energy = self._clamp(state["energy"] + energy_delta)
            new_loneliness = self._clamp(state["loneliness"] + loneliness_delta)
            conn.execute(
                """UPDATE persona_state
                   SET mood = ?, energy = ?, loneliness = ?, updated_at = ?
                   WHERE id = 1""",
                (
                    new_mood,
                    new_energy,
                    new_loneliness,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def adjust_mood(self, delta: int) -> None:
        self._update(delta, 0, 0)

    def adjust_energy(self, delta: int) -> None:
        self._update(0, delta, 0)

    def adjust_loneliness(self, delta: int) -> None:
        self._update(0, 0, delta)

    def tick(self) -> dict[str, int] | None:
        """根据时间流逝自动衰减状态，返回新状态（无变化则返回 None）。"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT mood, energy, loneliness, updated_at FROM persona_state WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            mood, energy, loneliness, updated_str = row[0], row[1], row[2], row[3]
            try:
                updated_at = datetime.fromisoformat(updated_str)
            except (ValueError, TypeError):
                return None

            now = datetime.now(timezone.utc)
            delta_sec = (now - updated_at.replace(tzinfo=timezone.utc)).total_seconds()

            d_energy = 0
            d_loneliness = 0

            if delta_sec > TICK_ENERGY_DECAY_SECONDS:
                d_energy = TICK_ENERGY_DECAY_DELTA
            if delta_sec > TICK_LONELINESS_12H_SECONDS:
                d_loneliness += TICK_LONELINESS_12H_DELTA
            if delta_sec > TICK_LONELINESS_24H_SECONDS:
                d_loneliness += TICK_LONELINESS_24H_DELTA

            if d_energy == 0 and d_loneliness == 0:
                return None

            new_energy = self._clamp(energy + d_energy)
            new_loneliness = self._clamp(loneliness + d_loneliness)

            conn.execute(
                """UPDATE persona_state
                   SET energy = ?, loneliness = ?, updated_at = ?
                   WHERE id = 1""",
                (new_energy, new_loneliness, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return {"mood": mood, "energy": new_energy, "loneliness": new_loneliness}
        finally:
            conn.close()

    def describe(self) -> str:
        """返回人类可读的状态描述，用于注入 Prompt。"""
        state = self.get_state()
        lines = [
            "当前状态：",
            f"心情：{_label(state['mood'], MOOD_LABELS)}",
            f"精力：{state['energy']}",
            f"孤独：{state['loneliness']}",
        ]
        return "\n".join(lines)
