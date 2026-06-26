"""人格系统：根据聊天对象身份和当前情绪，动态调整回复风格。
所有规则从 config/persona_rules.json 加载。
"""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "persona_rules.json"


def _load() -> dict:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


_cfg = _load()

_base = _cfg.get("base", {})
_identities = _cfg.get("identities", {})

BASE_SARCASM: int = int(_base.get("sarcasm", 5))
BASE_GENTLENESS: int = int(_base.get("gentleness", 5))
BASE_MAX_CHARS: int = int(_base.get("max_reply_chars", 120))

CATCHPHRASES: list[str] = _cfg.get("catchphrases", [])
LIKED_EXPRESSIONS: list[str] = _cfg.get("liked_expressions", [])
DISLIKED_EXPRESSIONS: list[str] = _cfg.get("disliked_expressions", [])


def _clamp(value: int) -> int:
    return max(1, min(10, value))


def _describe_level(value: int, scale: dict[int, str]) -> str:
    for threshold in sorted(scale, reverse=True):
        if value >= threshold:
            return scale[threshold]
    return scale.get(min(scale), "未知")


SARCASM_SCALE: dict[int, str] = {
    8: "很高 — 随便怼，嘴下不留情",
    6: "较高 — 可以吐槽玩梗",
    4: "中等 — 偶尔调侃",
    2: "较低 — 基本不说重话",
    1: "极低 — 完全不说负面话",
}

GENTLENESS_SCALE: dict[int, str] = {
    8: "很高 — 温柔体贴，主动关心",
    6: "较高 — 语气温和",
    4: "中等 — 不冷不热",
    2: "较低 — 比较随意",
    1: "极低 — 不怎么表达关心",
}


class PersonaService:
    """根据关系身份和情绪，返回当前人格状态描述，用于注入 Prompt。"""

    def describe(
        self,
        identity: str,
        mood_state: dict[str, int],
    ) -> str:
        """返回一段注入 Prompt 的人格描述。

        Args:
            identity: 用户身份标识（从 users.json 的 identity 字段获取）
            mood_state: MoodService.get_state() 的返回值
        """
        id_cfg = _identities.get(identity, {})
        sarcasm = _clamp(id_cfg.get("sarcasm", BASE_SARCASM))
        gentleness = _clamp(id_cfg.get("gentleness", BASE_GENTLENESS))
        max_chars = id_cfg.get("max_reply_chars", BASE_MAX_CHARS)
        style_hint = id_cfg.get("style", "")

        # 情绪微调：低心情时更毒舌，高心情时更温柔
        mood = mood_state.get("mood", 50)
        if mood < 30:
            sarcasm = _clamp(sarcasm + 1)
            gentleness = _clamp(gentleness - 1)
        elif mood > 75:
            sarcasm = _clamp(sarcasm - 1)
            gentleness = _clamp(gentleness + 1)

        sarcasm_label = _describe_level(sarcasm, SARCASM_SCALE)
        gentleness_label = _describe_level(gentleness, GENTLENESS_SCALE)

        lines = [
            "## 当前人格状态",
            f"- 毒舌程度：{sarcasm}/10 — {sarcasm_label}",
            f"- 温柔程度：{gentleness}/10 — {gentleness_label}",
            f"- 回复长度：不超过 {max_chars} 字",
        ]

        if style_hint:
            lines.append(f"- 特殊规则：{style_hint}")

        if mood < 30:
            lines.append("- 当前心情不好，回复可能更冷淡/毒舌")
        elif mood > 75:
            lines.append("- 当前心情很好，回复可能更热情/活泼")

        lines.append("")
        lines.append("## 表达偏好")
        lines.append(f"- 常用口头禅：{' / '.join(CATCHPHRASES[:6])}")
        lines.append(f"- 喜欢的表达：{'；'.join(LIKED_EXPRESSIONS)}")
        lines.append(f"- 不喜欢的表达：{'；'.join(DISLIKED_EXPRESSIONS)}")

        return "\n".join(lines)
