"""行为决策引擎：根据情绪、关系、记忆状态决定YHarvest的下一步行为。
只做决策，不发送消息，不调用 API。
配置从 config_service.py 加载。
"""

from __future__ import annotations

import random
from typing import Any

from .config_service import (
    W_CHAT,
    W_MEME,
    W_POKE,
    W_TOPIC,
    B_POKE_STREAK,
    D_EMOTION,
)


class BehaviorService:
    """行为决策服务。"""

    def decide_next_action(
        self,
        mood_state: dict[str, int],
        relation_data: dict[str, Any],
        memories: list[str],
    ) -> dict[str, Any]:
        """根据状态计算下一步行为的权重，返回决策结果。

        Args:
            mood_state: {"mood": int, "energy": int}
            relation_data: {"favorability": int, "intimacy": int, ...}
            memories: 长期记忆列表

        Returns:
            {"action": "chat/meme/poke/topic", "emotion": str, ...}
        """
        mood = mood_state.get("mood", 50)
        _energy = mood_state.get("energy", 50)  # noqa: F841
        _favor = relation_data.get("favorability", 50)  # noqa: F841
        intimacy = relation_data.get("intimacy", 0)

        # 基础权重
        weights = {
            "chat": W_CHAT,
            "meme": W_MEME,
            "poke": W_POKE,
            "topic": W_TOPIC,
        }

        # 调整权重
        if mood < 30:
            weights["chat"] += 10
        if intimacy > 10:
            weights["poke"] += int(intimacy * B_POKE_STREAK)
        if len(memories) > 5:
            weights["topic"] += 5

        # 随机选择
        total = sum(weights.values())
        roll = random.randint(1, total)
        cumulative = 0
        action = "chat"
        for act, w in weights.items():
            cumulative += w
            if roll <= cumulative:
                action = act
                break

        emotion = self.classify_emotion(mood_state)
        return {"action": action, "emotion": emotion}

    def classify_emotion(self, mood_state: dict[str, int]) -> str:
        """根据情绪状态返回分类标签。"""
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
        return D_EMOTION
