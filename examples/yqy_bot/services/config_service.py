"""统一配置管理服务。

提供所有配置文件的统一加载入口，带缓存，避免重复读取。
使用 pyproject.toml 作为项目根目录锚点。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def find_project_root() -> Path:
    """通过 pyproject.toml 锚点文件找到项目根目录。

    从当前文件向上搜索，直到找到包含 pyproject.toml 的目录。
    """
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").is_file():
            # 检查是否是 yqy_bot 的 pyproject.toml
            content = (parent / "pyproject.toml").read_text(encoding="utf-8")
            if "yqy-bot" in content or "yqy_bot" in content:
                return parent
    # 如果找不到，回退到 parents[1]（services 目录的父目录）
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = find_project_root()
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"


@lru_cache(maxsize=None)
def get_config(name: str) -> dict[str, Any]:
    """获取配置文件内容（带缓存）。

    Args:
        name: 配置文件名（不含 .json 后缀），如 "bot"、"prompt"、"mood"

    Returns:
        配置字典，文件不存在或解析失败时返回空字典
    """
    path = CONFIG_DIR / f"{name}.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def get_bot_config() -> dict[str, Any]:
    """获取 bot.json 配置。"""
    return get_config("bot")


def get_prompt_config() -> dict[str, Any]:
    """获取 prompt.json 配置。"""
    return get_config("prompt")


def get_mood_config() -> dict[str, Any]:
    """获取 mood.json 配置。"""
    return get_config("mood")


def get_relation_config() -> dict[str, Any]:
    """获取 relation.json 配置。"""
    return get_config("relation")


def get_fact_guard_config() -> dict[str, Any]:
    """获取 fact_guard.json 配置。"""
    return get_config("fact_guard")


def get_reflection_config() -> dict[str, Any]:
    """获取 reflection.json 配置。"""
    return get_config("reflection")


def get_users_config() -> dict[str, Any]:
    """获取 users.json 配置。"""
    return get_config("users")


def get_behavior_config() -> dict[str, Any]:
    """获取 behavior.json 配置。"""
    return get_config("behavior")


def get_persona_rules_config() -> dict[str, Any]:
    """获取 persona_rules.json 配置。"""
    return get_config("persona_rules")


def get_memes_config() -> dict[str, Any]:
    """获取 memes.json 配置。"""
    return get_config("memes")


def get_memory_filter_config() -> dict[str, Any]:
    """获取 memory_filter.json 配置。"""
    return get_config("memory_filter")


def get_active_life_config() -> dict[str, Any]:
    """获取 active_life.json 配置。"""
    return get_config("active_life")


def get_human_behavior_config() -> dict[str, Any]:
    """获取 human_behavior.json 配置。"""
    return get_config("human_behavior")


# 常用配置的便捷访问
BOT_CONFIG = get_bot_config()
PROMPT_CONFIG = get_prompt_config()
FACT_GUARD_CONFIG = get_fact_guard_config()

# 从配置中提取常用常量
MAX_HISTORY_TURNS: int = int(BOT_CONFIG.get("max_history_turns", 15))
MEME_API_URL: str = BOT_CONFIG.get("meme_api_url", "")
MEME_SAVE_KEYWORDS: tuple[str, ...] = tuple(BOT_CONFIG.get("meme_save_keywords", []))
MEME_SEND_KEYWORDS: tuple[str, ...] = tuple(BOT_CONFIG.get("meme_send_keywords", []))
MEME_DELETE_KEYWORDS: tuple[str, ...] = tuple(
    BOT_CONFIG.get("meme_delete_keywords", ["删表情", "删除表情", "删收藏"])
)

# 群聊回复配置
_GROUP_REPLY_CONFIG = BOT_CONFIG.get("group_reply", {})
GROUP_REPLY_ENABLED: bool = _GROUP_REPLY_CONFIG.get("enabled", True)
GROUP_REPLY_BASE_PROBABILITY: float = float(_GROUP_REPLY_CONFIG.get("base_probability", 0.15))
GROUP_REPLY_COOLDOWN_SECONDS: int = int(_GROUP_REPLY_CONFIG.get("cooldown_seconds", 60))
GROUP_REPLY_TRIGGER_KEYWORDS: tuple[str, ...] = tuple(
    _GROUP_REPLY_CONFIG.get("trigger_keywords", ["小瑶", "瑶瑶", "yqy", "YQY"])
)
GROUP_REPLY_CONTEXT_AWARE: bool = _GROUP_REPLY_CONFIG.get("context_aware_enabled", True)
GROUP_REPLY_MAX_PER_MINUTE: int = int(_GROUP_REPLY_CONFIG.get("max_replies_per_minute", 3))

# ═══════════════════════════════════════════
#  memory_filter 配置（已合并，JSON 文件可删除）
# ═══════════════════════════════════════════

_MEMORY_FILTER_CONFIG = get_memory_filter_config()
_MEMORY_FILTER_DEFAULTS = {
    "skip_keywords": [
        "哈哈",
        "哈哈哈",
        "牛逼",
        "卧槽",
        "在吗",
        "晚安",
        "早安",
        "收到",
        "OK",
        "ok",
        "好的",
    ],
    "min_text_length": 8,
    "question_marks": ["?", "？", "吗", "呢"],
}
SKIP_KEYWORDS: frozenset[str] = frozenset(
    _MEMORY_FILTER_CONFIG.get("skip_keywords", _MEMORY_FILTER_DEFAULTS["skip_keywords"])
)
MIN_TEXT_LENGTH: int = int(
    _MEMORY_FILTER_CONFIG.get(
        "min_text_length", _MEMORY_FILTER_DEFAULTS["min_text_length"]
    )
)
QUESTION_MARKS: frozenset[str] = frozenset(
    _MEMORY_FILTER_CONFIG.get(
        "question_marks", _MEMORY_FILTER_DEFAULTS["question_marks"]
    )
)

# ═══════════════════════════════════════════
#  behavior 配置（合并）
# ═══════════════════════════════════════════

_BEHAVIOR_CONFIG = get_behavior_config()
_behavior_weights = _BEHAVIOR_CONFIG.get("base_weights", {})
_behavior_thresholds = _BEHAVIOR_CONFIG.get("thresholds", {})
_behavior_boosts = _BEHAVIOR_CONFIG.get("boosts", {})
_behavior_defaults = _BEHAVIOR_CONFIG.get("defaults", {})

W_CHAT: int = int(_behavior_weights.get("chat", 60))
W_MEME: int = int(_behavior_weights.get("meme", 20))
W_POKE: int = int(_behavior_weights.get("poke", 5))
W_TOPIC: int = int(_behavior_weights.get("topic", 15))

T_MEME_BOOST: int = int(_behavior_thresholds.get("meme_boost", 3))
T_POKE_BOOST: int = int(_behavior_thresholds.get("poke_boost", 5))
T_POKE_COOLDOWN_HOURS: int = int(_behavior_thresholds.get("poke_cooldown_hours", 24))

B_MEME_STREAK: float = float(_behavior_boosts.get("meme_streak", 1.5))
B_POKE_STREAK: float = float(_behavior_boosts.get("poke_streak", 1.3))

D_EMOTION: str = _behavior_defaults.get("emotion", "teasing")

# ═══════════════════════════════════════════
#  persona_rules 配置（合并）
# ═══════════════════════════════════════════

_PERSONA_CONFIG = get_persona_rules_config()
_persona_base = _PERSONA_CONFIG.get("base", {})
_persona_identities = _PERSONA_CONFIG.get("identities", {})

BASE_SARCASM: int = int(_persona_base.get("sarcasm", 5))
BASE_GENTLENESS: int = int(_persona_base.get("gentleness", 5))
BASE_MAX_CHARS: int = int(_persona_base.get("max_reply_chars", 120))

CATCHPHRASES: list[str] = _PERSONA_CONFIG.get("catchphrases", [])
LIKED_EXPRESSIONS: list[str] = _PERSONA_CONFIG.get("liked_expressions", [])
DISLIKED_EXPRESSIONS: list[str] = _PERSONA_CONFIG.get("disliked_expressions", [])

# ═══════════════════════════════════════════
#  reflection 配置（已合并，JSON 文件可删除）
# ═══════════════════════════════════════════

_REFLECTION_CONFIG = get_reflection_config()
_REFLECTION_DEFAULTS = {
    "messages_before_reflection": 20,
    "max_reflections_per_day": 3,
    "max_recent_reflections": 10,
}
MESSAGES_BEFORE_REFLECTION: int = int(
    _REFLECTION_CONFIG.get("messages_before_reflection", _REFLECTION_DEFAULTS["messages_before_reflection"])
)
MAX_REFLECTIONS_PER_DAY: int = int(
    _REFLECTION_CONFIG.get("max_reflections_per_day", _REFLECTION_DEFAULTS["max_reflections_per_day"])
)
MAX_RECENT_REFLECTIONS: int = int(
    _REFLECTION_CONFIG.get("max_recent_reflections", _REFLECTION_DEFAULTS["max_recent_reflections"])
)

# ═══════════════════════════════════════════
#  active_life 配置（合并）
# ═══════════════════════════════════════════

_ACTIVE_LIFE_CONFIG = get_active_life_config()
ACTIVE_CHAT_MIN_INTERVAL_HOURS: int = int(
    _ACTIVE_LIFE_CONFIG.get("min_interval_hours", 2)
)
ACTIVE_CHAT_MAX_INTERVAL_HOURS: int = int(
    _ACTIVE_LIFE_CONFIG.get("max_interval_hours", 72)
)
ACTIVE_CHAT_PROBABILITY: float = float(_ACTIVE_LIFE_CONFIG.get("probability", 0.15))
BANNED_PHRASES: list[str] = _ACTIVE_LIFE_CONFIG.get("banned_phrases", [])

# 主动行为配置
TICK_INTERVAL: int = int(_ACTIVE_LIFE_CONFIG.get("tick_interval_seconds", 600))
LONELINESS_THRESHOLD: int = int(_ACTIVE_LIFE_CONFIG.get("loneliness_threshold", 70))
ENERGY_THRESHOLD: int = int(_ACTIVE_LIFE_CONFIG.get("energy_threshold", 30))
COOLDOWN_HOURS: int = int(_ACTIVE_LIFE_CONFIG.get("cooldown_hours", 4))
RECENT_HISTORY_TURNS: int = int(_ACTIVE_LIFE_CONFIG.get("recent_history_turns", 10))
BLOCKED_TARGETS: set[str] = set(_ACTIVE_LIFE_CONFIG.get("blocked_targets", []))

_post = _ACTIVE_LIFE_CONFIG.get("post_action", {})
POST_ACTION_ENERGY_DELTA: int = int(_post.get("energy_delta", -2))
POST_ACTION_LONELINESS_DELTA: int = int(_post.get("loneliness_delta", -5))
