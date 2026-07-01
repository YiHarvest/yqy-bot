"""事实闸门服务：代码层拦截无依据的编造内容。

从 chat.py 中提取，包含毒性过滤和事实闸门检查。
使用正则匹配增强检测能力。
"""

from __future__ import annotations

import re
from typing import Any

from iamai import LLMClient, LLMConfig
from loguru import logger

from .config_service import FACT_GUARD_CONFIG

# ═══════════════════════════════════════════
#  配置加载
# ═══════════════════════════════════════════

_HIGH_RISK_TRIGGERS: list[str] = FACT_GUARD_CONFIG.get("high_risk_triggers", [])
_TOXIC_PATTERNS: list[str] = FACT_GUARD_CONFIG.get("toxic_patterns", [])
_FALLBACK_REPLY: dict[str, Any] = FACT_GUARD_CONFIG.get(
    "fallback_reply",
    {"text": "行，我收敛点。", "face_id": "", "send_meme": False},
)
_TOXIC_FALLBACK: dict[str, Any] = FACT_GUARD_CONFIG.get(
    "toxic_fallback",
    {"text": "抱歉，我注意语气。", "face_id": "", "send_meme": False},
)
_REWRITE_PROMPT: str = FACT_GUARD_CONFIG.get(
    "rewrite_prompt",
    "请删除无依据细节，只保留基于当前用户消息的调侃。最多一句话。返回JSON。",
)


# ═══════════════════════════════════════════
#  正则模式：增强检测能力
# ═══════════════════════════════════════════

# 时间相关模式：检测 "上次"、"昨天"、"三小时" 等
_TIME_PATTERNS = [
    r"上次",
    r"昨天",
    r"前天",
    r"刚才",
    r"每次",
    r"之前",
    r"\d+小时",
    r"\d+分钟前",
    r"\d+天前",
    r"\d+周前",
    r"昨晚",
    r"今早",
]

# 数字夸张模式：检测 "百条"、"三小时"、"零下五度" 等
_EXAGGERATED_NUMBER_PATTERNS = [
    r"百[条个分种]",
    r"上千",
    r"无数",
    r"零下\d+度",
    r"\d+度(?![表示])",  # 数字度，排除"表示"
]

# 具体名词模式：检测 "河童"、"短发"、"红包" 等
_SPECIFIC_NOUN_PATTERNS = [
    r"河童",
    r"短发",
    r"发型",
    r"红包",
    r"聊天记录",
]

# 编译所有正则模式
_ALL_REGEX_PATTERNS = [
    *(_TIME_PATTERNS),
    *(_EXAGGERATED_NUMBER_PATTERNS),
    *(_SPECIFIC_NOUN_PATTERNS),
]


# ═══════════════════════════════════════════
#  毒性过滤
# ═══════════════════════════════════════════


def check_toxic(text: str) -> str | None:
    """检查回复是否包含毒性内容。

    Args:
        text: AI 回复文本

    Returns:
        匹配到的毒性模式，无毒返回 None
    """
    if not text:
        return None
    for pat in _TOXIC_PATTERNS:
        if pat in text:
            return pat
    return None


def get_toxic_fallback() -> dict[str, Any]:
    """返回毒性拦截的兜底回复。"""
    return dict(_TOXIC_FALLBACK)


# ═══════════════════════════════════════════
#  事实闸门
# ═══════════════════════════════════════════


def build_evidence_text(
    current_user_message: str,
    user_history_messages: list[str],
    memories: list[str],
    social_memories: list[str],
) -> str:
    """构建事实证据文本（仅 user 方信息，不含 assistant 历史）。

    Args:
        current_user_message: 当前用户消息
        user_history_messages: 历史用户消息列表
        memories: 长期记忆列表
        social_memories: 社交记忆内容列表

    Returns:
        合并后的证据文本
    """
    parts: list[str] = [current_user_message]
    parts.extend(user_history_messages)
    parts.extend(memories)
    parts.extend(social_memories)
    return "\n".join(parts)


def check_unsupported_claims(text: str, evidence_text: str) -> list[str]:
    """检查回复是否包含无依据的高风险触发词。

    使用词表匹配 + 正则匹配双重检测。

    Args:
        text: AI 回复文本
        evidence_text: 事实证据文本

    Returns:
        在 text 中出现但不在 evidence_text 中的风险触发词列表
    """
    if not text:
        return []

    unsupported: list[str] = []

    # 1. 词表匹配
    for trigger in _HIGH_RISK_TRIGGERS:
        if trigger in text and trigger not in evidence_text:
            unsupported.append(trigger)

    # 2. 正则匹配（增强检测）
    for pattern in _ALL_REGEX_PATTERNS:
        matches = re.findall(pattern, text)
        for match in matches:
            match_str = str(match)
            if match_str not in evidence_text and match_str not in unsupported:
                unsupported.append(match_str)

    return unsupported


def check_risk_patterns(text: str) -> list[str]:
    """正则检测风险模式（用于额外的风险检测）。

    Args:
        text: 待检测文本

    Returns:
        匹配到的风险模式列表
    """
    risks: list[str] = []
    for pattern in _ALL_REGEX_PATTERNS:
        matches = re.findall(pattern, text)
        for match in matches:
            match_str = str(match)
            if match_str not in risks:
                risks.append(match_str)
    return risks


async def rewrite_unsafe_reply(
    original_text: str,
    unsupported_words: list[str],
    current_user_message: str,
) -> dict[str, Any]:
    """调用 LLM 重写包含无依据事实的回复。

    Args:
        original_text: 原始 AI 回复
        unsupported_words: 无依据的触发词
        current_user_message: 当前用户消息

    Returns:
        重写后的 reply dict，失败则返回兜底回复
    """
    config = LLMConfig.from_mapping()
    prompt = (
        f"{_REWRITE_PROMPT}\n\n"
        f"当前用户消息：{current_user_message}\n\n"
        f"以下回复包含无依据的细节（{', '.join(unsupported_words)}），请重写：\n"
        f"原始回复：{original_text}\n\n"
        "重写后的 JSON："
    )
    try:
        return await LLMClient(config).chat_json([{"role": "user", "content": prompt}])
    except Exception:
        logger.warning("事实闸门重写失败，使用兜底回复")
        return dict(_FALLBACK_REPLY)


def get_fallback_reply() -> dict[str, Any]:
    """返回事实闸门的兜底回复。"""
    return dict(_FALLBACK_REPLY)


# ═══════════════════════════════════════════
#  解析工具
# ═══════════════════════════════════════════


def parse_result(result: dict[str, Any] | list[Any]) -> tuple[str, str, bool, str]:
    """解析 LLM 返回的结果。

    Args:
        result: LLM 返回的结果（dict 或 list）

    Returns:
        (text, face_id, send_meme, at_user_id) 四元组
    """
    text = ""
    face_id = ""
    send_meme = False

    src: dict[str, Any] = {}
    if isinstance(result, dict):
        src = result
    elif isinstance(result, list) and len(result) > 0:
        src = result[0] if isinstance(result[0], dict) else {}

    text = str(src.get("text", ""))
    raw_face = str(src.get("face_id", ""))
    face_id = raw_face.strip() if raw_face.strip().isdigit() else ""
    send_meme = bool(src.get("send_meme", False))
    at_user_id = str(src.get("at_user_id", "")).strip()

    return text, face_id, send_meme, at_user_id
