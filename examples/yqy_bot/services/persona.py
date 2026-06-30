"""人格系统：根据聊天对象身份和当前情绪，动态调整回复风格。
配置从 config_service.py 加载。
"""

from __future__ import annotations


from .config_service import (
    BASE_SARCASM,
    BASE_GENTLENESS,
    BASE_MAX_CHARS,
    CATCHPHRASES,
    _persona_identities,
)


class PersonaService:
    """人格调整服务。"""

    def describe(self, identity: str, mood_state: dict[str, int]) -> str:
        """根据身份和情绪生成人格描述文本。

        Args:
            identity: 用户身份（yqy/妹妹/...）
            mood_state: {"mood": int, "energy": int}

        Returns:
            人格描述文本
        """
        mood = mood_state.get("mood", 50)

        # 从配置获取身份特定参数
        identity_cfg = _persona_identities.get(identity, {})
        sarcasm = identity_cfg.get("sarcasm", BASE_SARCASM)
        gentleness = identity_cfg.get("gentleness", BASE_GENTLENESS)
        max_chars = identity_cfg.get("max_reply_chars", BASE_MAX_CHARS)

        # 情绪影响
        if mood > 70:
            sarcasm = max(1, sarcasm - 2)
        elif mood < 30:
            gentleness = max(1, gentleness - 2)

        parts = [f"当前回复风格：讽刺度 {sarcasm}/10，温和度 {gentleness}/10"]
        parts.append(f"单次回复不超过 {max_chars} 字")

        if CATCHPHRASES:
            parts.append(f"口头禅：{', '.join(CATCHPHRASES[:3])}")

        return "\n".join(parts)
