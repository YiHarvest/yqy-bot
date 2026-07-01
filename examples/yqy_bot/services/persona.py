"""人格系统：根据聊天对象身份和当前情绪，动态调整回复风格。
配置从 config_service.py 加载。
"""

from __future__ import annotations

import random

from .config_service import (
    BASE_SARCASM,
    BASE_GENTLENESS,
    BASE_MAX_CHARS,
    BANNED_CATCHPHRASES,
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

        parts: list[str] = []

        # 多样化模板：避免每次都用同一套固定文字
        templates = [
            f"当前回复风格：讽刺度 {sarcasm}/10，温和度 {gentleness}/10",
            f"回复风格：讽刺 {sarcasm}/10，温和 {gentleness}/10，不超过 {max_chars} 字",
            f"风格倾向：讽刺度{sarcasm}、温和度{gentleness}，回复控制在 {max_chars} 字内",
        ]
        parts.append(random.choice(templates))
        parts.append(f"单次回复不超过 {max_chars} 字")

        # 禁止使用的口头禅（最高优先级约束）
        if BANNED_CATCHPHRASES:
            banned_str = ', '.join(BANNED_CATCHPHRASES[:5])
            parts.append(f"禁止使用以下口头禅：{banned_str}。出现这些词的回复会被拦截重写。")

        return "\n".join(parts)