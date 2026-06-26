"""行为决策引擎：根据情绪、关系、记忆状态决定星星的下一步行为。
所有参数从 config/behavior.json 加载。
只做决策，不发送消息，不调用 API。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "behavior.json"


def _load() -> dict:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


_cfg = _load()

_weights = _cfg.get("base_weights", {})
_thresholds = _cfg.get("thresholds", {})
_boosts = _cfg.get("boosts", {})
_defaults = _cfg.get("defaults", {})

W_CHAT: int = int(_weights.get("chat", 60))
W_MEME: int = int(_weights.get("meme", 20))
W_POKE: int = int(_weights.get("poke", 10))

TH_ENERGY_LOW: int = int(_thresholds.get("energy_low", 20))
TH_LONELINESS_HIGH: int = int(_thresholds.get("loneliness_high", 80))
TH_MOOD_HIGH: int = int(_thresholds.get("mood_high", 80))
TH_FAVORABILITY_HIGH: int = int(_thresholds.get("favorability_high", 80))
TH_ENERGY_TIRED: int = int(_thresholds.get("energy_tired", 40))

BOOST_CHAT: int = int(_boosts.get("chat_loneliness_boost", 30))
BOOST_MEME: int = int(_boosts.get("meme_mood_boost", 30))
BOOST_POKE: int = int(_boosts.get("poke_favorability_boost", 20))

DEFAULT_MOOD: int = int(_defaults.get("mood", 50))
DEFAULT_ENERGY: int = int(_defaults.get("energy", 50))
DEFAULT_LONELINESS: int = int(_defaults.get("loneliness", 20))
DEFAULT_FAVORABILITY: int = int(_defaults.get("favorability", 50))


class BehaviorService:
    """行为决策引擎。"""

    @staticmethod
    def decide_next_action(
        mood_state: dict[str, int],
        relation_state: dict[str, Any] | None,
        memory_state: list[str] | None,
    ) -> dict[str, str]:
        """根据当前状态返回推荐的行为。

        返回格式：{"action": "chat|meme|poke|silent", "reason": "...", "emotion": "happy|teasing|comfort|angry|excited"}
        """
        mood = mood_state.get("mood", DEFAULT_MOOD)
        energy = mood_state.get("energy", DEFAULT_ENERGY)
        loneliness = mood_state.get("loneliness", DEFAULT_LONELINESS)
        favorability = relation_state.get("favorability", DEFAULT_FAVORABILITY) if relation_state else DEFAULT_FAVORABILITY

        # 情绪分类：happy / teasing / comfort / angry / excited
        if mood > 80:
            emotion = "excited"
        elif mood > 65:
            emotion = "happy"
        elif mood < 25:
            emotion = "angry"
        elif energy < 25:
            emotion = "angry"
        else:
            emotion = "teasing"

        if favorability > 85:
            emotion = "comfort"

        if energy < TH_ENERGY_LOW:
            return {"action": "silent", "reason": "low_energy", "emotion": emotion}

        w_chat = W_CHAT
        w_meme = W_MEME
        w_poke = W_POKE

        if loneliness > TH_LONELINESS_HIGH:
            w_chat += BOOST_CHAT
        if mood > TH_MOOD_HIGH:
            w_meme += BOOST_MEME
        if favorability > TH_FAVORABILITY_HIGH:
            w_poke += BOOST_POKE

        pool = (
            ["chat"] * w_chat
            + ["meme"] * w_meme
            + ["poke"] * w_poke
        )
        action = random.choice(pool)

        reasons: list[str] = []
        if loneliness > TH_LONELINESS_HIGH:
            reasons.append("high_loneliness")
        if mood > TH_MOOD_HIGH:
            reasons.append("high_mood")
        if favorability > TH_FAVORABILITY_HIGH:
            reasons.append("high_favorability")
        if energy < TH_ENERGY_TIRED:
            reasons.append("tired")

        return {"action": action, "reason": ",".join(reasons) if reasons else "default", "emotion": emotion}

    @staticmethod
    def classify_emotion(mood_state: dict[str, int]) -> str:
        """独立情绪分类（供需要单独获取情绪时调用）。"""
        mood = mood_state.get("mood", 50)
        energy = mood_state.get("energy", 50)
        if mood > 80:
            return "excited"
        if mood > 65:
            return "happy"
        if mood < 25:
            return "angry"
        if energy < 25:
            return "angry"
        return "teasing"
