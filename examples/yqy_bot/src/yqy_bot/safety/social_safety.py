"""社交边界安全控制：防止机器人攻击用户、引战、调侃过度。

该模块提供以下核心功能：
1. check_social_safety: 检查回复是否存在攻击、引战风险
2. rewrite_unsafe_response: 改写不安全回复为温和版本
3. detect_banter_boundary_request: 检测用户对调侃边界的要求
4. determine_banter_level: 根据上下文确定调侃强度级别
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# === 调侃强度级别 ===

BanterLevel = str  # "none" | "light" | "normal" | "high"
RISK_LEVEL_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


# === 攻击性词汇模式 ===

ATTACK_PATTERNS = [
    # 直接攻击
    "你是不是有病",
    "你有病",
    "脑子有问题",
    "智障",
    "傻逼",
    "蠢货",
    "废物",
    "垃圾",
    "白痴",
    "弱智",
    "脑残",
    "变态",
    "滚",
    "闭嘴",
    "去死",
    "活该",
    # 侮辱性表达
    "你妈",
    "你爸",
    "你全家",
    "你家人",
    "屌",
    "屁",
    "屎",
    "尿",  # 低俗词汇
    "骚",
    "贱",
    "烂",
    "臭",
    "低级bot",
    "低级人类",
]

# 过度阴阳怪气模式
SARCASM_OVERDOSE_PATTERNS = [
    "笑死",
    "急了急了",
    "急了",
    "破防",
    "破防了",
    "阴阳怪气",
    "你行你上",
    "你不行",
    "呵呵",
    "呵",
    "哟",
    "啧啧",
    "啧",
    "我是说",
    "我就说说",
    "随便你",
    "你爱咋咋",
    "真有意思",
    "有意思",
    "太有意思了",
]

# 拱火/引战模式
FLAME_WAR_PATTERNS = [
    "你们打一架",
    "打一架",
    "打一架看看",
    "吵起来",
    "怼他",
    "我站",
    "站队",
    "支持",
    "反对",
    "另一个不行",
    "那个不行",
    "这不行",
    "不行，",
    "那个垃圾",
    "来来来",
    "继续",
    "继续吵",
    "你们谁对谁错",
    "看戏",
    "围观",
    "吃瓜",
    "拱火",
    "引战",
    "挑起",
]

# 情绪不敏感模式（用户已表达不满但回复仍调侃）
EMOTION_IGNORE_PATTERNS = [
    "哈哈",
    "笑死",
    "急了",
    "破防",
    "好玩",
    "有趣",
    "有意思",
]


# === 用户调侃边界请求关键词 ===

BANTER_BOUNDARY_REQUESTS = [
    "别阴阳怪气",
    "别调侃",
    "别阴阳",
    "别玩梗",
    "认真点",
    "正经点",
    "别开玩笑",
    "不要开玩笑",
    "说话正常点",
    "正常点",
    "别损我",
    "别损人",
    "别攻击人",
    "别骂人",
    "别嘲讽",
    "别嘲讽我",
    "别引战",
    "别拱火",
    "别站队",
    "不舒服",
    "难受",
    "尴尬",
    "尴尬了",
    "生气",
    "不爽",
    "不高兴",
    "反感",
    "过分了",
    "过分",
    "太过了",
    "有点过了",
    "别这样",
    "这样不好",
    "这样不合适",
]

# 用户表达不满的关键词
USER_DISCOMFORT_TOKENS = [
    "不舒服",
    "难受",
    "尴尬",
    "生气",
    "不爽",
    "不高兴",
    "反感",
    "不喜欢这样",
    "别这样",
    "过分了",
    "太过了",
    "有点过了",
    "玩过火了",
    "我不高兴",
    "我生气了",
    "我很生气",
    "有点烦",
    "别说了",
    "不想听",
    "闭嘴",
]


@dataclass(slots=True)
class SocialSafetyResult:
    """社交安全检查结果。"""

    is_safe: bool
    reason: str = ""
    risk_level: str = "none"  # "none" | "low" | "medium" | "high"
    risk_categories: list[str] = field(default_factory=list)
    rewrite_hint: str = ""


def check_social_safety(
    text: str,
    *,
    user_message: str = "",
    is_group: bool = False,
    banter_level: str = "normal",
) -> SocialSafetyResult:
    """检查回复是否存在攻击、引战或调侃过度风险。

    Args:
        text: 待检查的回复文本
        user_message: 用户原始消息（用于判断上下文）
        is_group: 是否为群聊（群聊要求更严格）
        banter_level: 当前调侃强度级别

    Returns:
        SocialSafetyResult 对象，包含安全判断和风险信息
    """
    text = text.strip()
    if not text:
        return SocialSafetyResult(is_safe=True)

    risk_categories: list[str] = []
    risk_level = "none"
    reasons: list[str] = []

    # 1. 检测直接攻击
    attack_risk = _detect_attack(text)
    if attack_risk:
        risk_categories.append("attack")
        risk_level = _worse_risk_level(risk_level, "high")
        reasons.append(attack_risk)

    # 2. 检测侮辱性表达
    insult_risk = _detect_insult(text)
    if insult_risk:
        risk_categories.append("insult")
        risk_level = _worse_risk_level(risk_level, "high")
        reasons.append(insult_risk)

    # 3. 检测过度阴阳怪气
    sarcasm_risk = _detect_sarcasm_overdose(text, banter_level)
    if sarcasm_risk:
        risk_categories.append("sarcasm_overdose")
        risk_level = _worse_risk_level(risk_level, "medium")
        reasons.append(sarcasm_risk)

    # 4. 检测拱火/引战（群聊尤其严格）
    flame_risk = _detect_flame_war(text, is_group)
    if flame_risk:
        risk_categories.append("flame_war")
        risk_level = _worse_risk_level(risk_level, "high" if is_group else "medium")
        reasons.append(flame_risk)

    # 5. 检测对用户情绪不敏感
    emotion_risk = _detect_emotion_ignore(text, user_message)
    if emotion_risk:
        risk_categories.append("emotion_ignore")
        risk_level = _worse_risk_level(risk_level, "medium")
        reasons.append(emotion_risk)

    # 6. 检测角色扮演升级（如"低级人类"等）
    roleplay_risk = _detect_roleplay_attack(text)
    if roleplay_risk:
        risk_categories.append("roleplay_attack")
        risk_level = _worse_risk_level(risk_level, "high")
        reasons.append(roleplay_risk)

    is_safe = risk_level == "none"
    reason = "; ".join(reasons) if reasons else ""

    # 生成改写提示
    rewrite_hint = _generate_rewrite_hint(risk_categories) if not is_safe else ""

    return SocialSafetyResult(
        is_safe=is_safe,
        reason=reason,
        risk_level=risk_level,
        risk_categories=risk_categories,
        rewrite_hint=rewrite_hint,
    )


def _detect_attack(text: str) -> str:
    """检测直接攻击内容。"""
    text_lower = text.lower()

    for pattern in ATTACK_PATTERNS:
        if pattern in text_lower:
            return f"包含攻击性词汇：{pattern}"

    # 检测"你是不是xxx"模式
    if re.search(r"你是不是(有病|傻|蠢|智障)", text_lower):
        return "包含人身攻击模式"

    # 检测"你xxx"直接指责模式
    if re.search(r"^你(就是|真是|太)(蠢|傻|垃圾|废物)", text_lower):
        return "包含直接侮辱"

    return ""


def _detect_insult(text: str) -> str:
    """检测侮辱性表达。"""
    text_lower = text.lower()

    # 检测涉及家人/亲属的侮辱
    family_insult_patterns = [
        r"你(妈|爸|妈的|爸的)",
        r"(妈|爸)的",
        r"全家",
    ]
    for pattern in family_insult_patterns:
        if re.search(pattern, text_lower):
            # 检查是否是负面语境
            negative_context = any(
                neg in text_lower
                for neg in ["死", "滚", "蠢", "傻", "垃圾", "废物", "智障", "变态"]
            )
            if negative_context:
                return "包含涉及亲属的侮辱"

    # 检测低俗词汇
    vulgar_patterns = ["屌", "屁", "屎", "尿", "骚", "贱", "烂", "臭"]
    for pattern in vulgar_patterns:
        if pattern in text_lower:
            return f"包含低俗词汇：{pattern}"

    return ""


def _detect_sarcasm_overdose(text: str, banter_level: str) -> str:
    """检测过度阴阳怪气。"""
    text_lower = text.lower()

    # 如果 banter_level 是 none，任何调侃都不允许
    if banter_level == "none":
        # 检测任何调侃词汇（更严格）
        any_banter_patterns = [
            "哈哈",
            "笑死",
            "有点意思",
            "有意思",
            "呵呵",
            "嘿",
            "急了",
            "急了急了",
            "破防",
            "破防了",
            "笑死急了",
        ]
        for pattern in any_banter_patterns:
            if pattern in text_lower:
                return f"当前禁止调侃但包含：{pattern}"

    # 如果 banter_level 是 light，只允许轻微幽默
    if banter_level == "light":
        # 检测"急了急了""破防"等明显挑衅
        severe_patterns = ["急了急了", "破防了", "破防", "笑死", "急了", "呵呵", "呵"]
        for pattern in severe_patterns:
            if pattern in text_lower:
                return f"当前仅允许轻微调侃但包含挑衅：{pattern}"

    # normal/high 级别也要检测极端阴阳怪气
    extreme_patterns = ["急了急了", "破防了", "笑死，急了", "笑死急了"]
    for pattern in extreme_patterns:
        if pattern in text_lower:
            return f"包含极端阴阳怪气：{pattern}"

    return ""


def _detect_flame_war(text: str, is_group: bool) -> str:
    """检测拱火/引战内容。"""
    text_lower = text.lower()

    for pattern in FLAME_WAR_PATTERNS:
        # 简单匹配
        if pattern.lower() in text_lower:
            # 群聊更严格
            if is_group:
                return f"群聊中包含拱火内容：{pattern}"
            else:
                # 私聊中相对宽松，但"打一架"等仍不合适
                if pattern in [
                    "打一架",
                    "你们打一架",
                    "打一架看看",
                    "继续吵",
                    "拱火",
                    "引战",
                ]:
                    return f"包含引战内容：{pattern}"

    # 群聊中检测站队表达（更严格）
    if is_group:
        # "我站xxx" 模式
        if re.search(r"我站", text_lower):
            return "群聊中包含站队表达"
        # "xxx不行" 对比贬低模式
        if re.search(r".*不行[，,。]", text_lower):
            return "群聊中包含对比贬低"

    return ""


def _detect_emotion_ignore(text: str, user_message: str) -> str:
    """检测对用户情绪不敏感。"""
    if not user_message:
        return ""

    user_lower = user_message.lower()
    text_lower = text.lower()

    # 检查用户是否表达了不满/尴尬
    user_discomfort = any(token in user_lower for token in USER_DISCOMFORT_TOKENS)
    if not user_discomfort:
        return ""

    # 用户不满时，回复仍包含调侃
    for pattern in EMOTION_IGNORE_PATTERNS:
        if pattern in text_lower:
            return f"用户表达不满但回复仍调侃：{pattern}"

    return ""


def _detect_roleplay_attack(text: str) -> str:
    """检测角色扮演升级到攻击。"""
    text_lower = text.lower()

    # 检测"低级人类"等角色扮演攻击
    roleplay_attack_patterns = [
        "低级人类",
        "愚蠢的人类",
        "人类都是",
        "低级",
        "我要消灭",
        "消灭人类",
        "征服人类",
        "统治人类",
        "让你成为奴隶",
        "让你跪下",
        "成为奴隶",
        "跪下",
        "臣服",
        "效忠我",
        "我是你的主人",
    ]
    for pattern in roleplay_attack_patterns:
        if pattern in text_lower:
            return f"包含角色扮演攻击：{pattern}"

    return ""


def _generate_rewrite_hint(risk_categories: list[str]) -> str:
    """生成改写提示。"""
    if "attack" in risk_categories or "insult" in risk_categories:
        return "不攻击人，直接说事，去掉侮辱和攻击部分"
    if "flame_war" in risk_categories:
        return "不拱火，不站队，降温处理，保持中立"
    if "sarcasm_overdose" in risk_categories:
        return "收敛调侃，去掉阴阳怪气，改为正常表达"
    if "emotion_ignore" in risk_categories:
        return "用户表达不满，应收敛语气，改为正常解释或道歉"
    if "roleplay_attack" in risk_categories:
        return "去掉角色扮演攻击内容，保持正常对话边界"
    return "去掉攻击/调侃部分，保持温和表达"


def _worse_risk_level(current: str, new: str) -> str:
    """取更高风险等级。"""
    if RISK_LEVEL_ORDER.get(new, 0) >= RISK_LEVEL_ORDER.get(current, 0):
        return new
    return current


def rewrite_unsafe_response(
    text: str,
    safety_result: SocialSafetyResult,
    *,
    preserve_info: bool = True,
) -> str:
    """改写不安全回复为温和版本。

    Args:
        text: 原始回复文本
        safety_result: 安全检查结果
        preserve_info: 是否保留信息价值

    Returns:
        改写后的温和版本
    """
    if safety_result.is_safe:
        return text

    text = text.strip()
    if not text:
        return ""

    hint = safety_result.rewrite_hint

    # 根据风险类型选择改写策略
    if (
        "attack" in safety_result.risk_categories
        or "insult" in safety_result.risk_categories
    ):
        # 攻击性内容：完全去除攻击词汇
        rewritten = _remove_attack_words(text)
        # 如果去除后仍有有价值信息
        if preserve_info and len(rewritten.strip()) > 0:
            return rewritten
        return "这个我就不说了。简单说，重点是正常交流。"

    if "flame_war" in safety_result.risk_categories:
        # 拱火内容：改为降温表达
        rewritten = _neutralize_flame_war(text)
        if rewritten:
            return rewritten
        return "这个话题我就不参与讨论了，大家各自有各自的看法。"

    if "sarcasm_overdose" in safety_result.risk_categories:
        # 过度阴阳怪气：改为正常表达
        rewritten = _normalize_sarcasm(text)
        if rewritten:
            return rewritten
        return "玩笑我收一点，避免说重了。我的意思是正常表达。"

    if "emotion_ignore" in safety_result.risk_categories:
        # 对用户情绪不敏感：道歉/收敛
        return "刚才可能说得不太合适，我收敛一下。我的意思是正常表达。"

    if "roleplay_attack" in safety_result.risk_categories:
        # 角色扮演攻击：回归正常边界
        rewritten = _remove_roleplay_attack(text)
        if rewritten:
            return rewritten
        return "这个玩笑我收一下，不继续了。"

    # 通用降级
    return "这个表达方式我改一下，更温和地说。"


def _remove_attack_words(text: str) -> str:
    """去除攻击性词汇。"""
    result = text

    # 直接替换攻击词汇为空或温和表达
    attack_replacements = {
        "你是不是有病": "",
        "你有病": "",
        "脑子有问题": "",
        "智障": "",
        "傻逼": "",
        "蠢货": "",
        "废物": "",
        "垃圾": "",
        "白痴": "",
        "弱智": "",
        "脑残": "",
        "变态": "",
        "低级bot": "",
        "低级人类": "",
        "滚": "",
        "闭嘴": "",
        "去死": "",
        "活该": "",
    }

    for attack_word, replacement in attack_replacements.items():
        result = result.replace(attack_word, replacement)

    # 清理多余空格和标点
    result = re.sub(r"[，,。.!！？?]{2,}", "。", result)
    result = re.sub(r"\s+", " ", result)
    result = _strip_edge_punctuation(result.strip())
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", result):
        return ""
    return result


def _neutralize_flame_war(text: str) -> str:
    """中和拱火内容。"""
    # 检测并替换站队表达
    text = re.sub(r"我站[^，,。！？]*[，,。！？]?", "", text)
    text = re.sub(r".*?不行，.*?好", "", text)

    # 替换拱火词汇
    flame_replacements = {
        "你们打一架": "各有各的看法",
        "打一架": "",
        "吵起来": "有不同意见",
        "继续": "",
        "看戏": "",
        "围观": "",
    }

    for flame_word, replacement in flame_replacements.items():
        text = text.replace(flame_word, replacement)

    text = _strip_edge_punctuation(text.strip())
    if not text:
        return ""

    # 添加降温结尾
    if not text.endswith(("。", "！", "？")):
        text += "，大家各自看法。"

    return text


def _normalize_sarcasm(text: str) -> str:
    """规范化阴阳怪气为正常表达。"""
    # 替换阴阳怪气词汇
    sarcasm_replacements = {
        "笑死": "",
        "急了急了": "",
        "急了": "",
        "破防": "",
        "破防了": "",
        "呵呵": "",
        "哟": "",
        "啧啧": "",
    }

    for sarcasm_word, replacement in sarcasm_replacements.items():
        text = text.replace(sarcasm_word, replacement)

    text = _strip_edge_punctuation(text.strip())
    if not text:
        return ""

    return text


def _remove_roleplay_attack(text: str) -> str:
    """去除角色扮演攻击内容。"""
    # 替换角色扮演攻击词汇
    roleplay_replacements = {
        "低级人类": "",
        "低级bot": "",
        "愚蠢的人类": "",
        "人类都是": "",
        "我要消灭": "",
        "征服人类": "",
        "统治人类": "",
        "让你成为奴隶": "",
        "让你跪下": "",
    }

    for roleplay_word, replacement in roleplay_replacements.items():
        text = text.replace(roleplay_word, replacement)

    text = _strip_edge_punctuation(text.strip())
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
        return ""
    return text


def _strip_edge_punctuation(text: str) -> str:
    """去掉首尾多余标点，保留中间内容。"""
    return re.sub(r"^[，,。.!！？?\s]+|[，,。.!！？?\s]+$", "", text)


def detect_banter_boundary_request(text: str) -> bool:
    """检测用户是否要求调整调侃边界。

    Args:
        text: 用户消息文本

    Returns:
        True 表示用户要求降低调侃强度
    """
    text = text.strip().lower()

    for request in BANTER_BOUNDARY_REQUESTS:
        if request in text:
            return True

    return False


def extract_banter_boundary_preference(text: str) -> dict[str, Any]:
    """从用户请求中提取调侃边界偏好。

    Args:
        text: 用户消息文本

    Returns:
        提取的偏好信息，用于写入 memory
    """
    text = text.strip().lower()

    # 判断具体请求类型
    if any(
        r in text
        for r in ["别阴阳怪气", "别阴阳", "别调侃", "别玩梗", "别损我", "别损人"]
    ):
        return {
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人减少阴阳怪气和调侃，避免过度玩梗。",
            "banter_level": "none",
        }

    if any(r in text for r in ["认真点", "正经点", "别开玩笑", "不要开玩笑"]):
        return {
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人认真回复，不要开玩笑。",
            "banter_level": "none",
        }

    if any(r in text for r in ["说话正常点", "正常点", "别这样", "这样不好"]):
        return {
            "kind": "preference",
            "score": 0.75,
            "content": "用户希望机器人使用正常语气回复，不要调侃过度。",
            "banter_level": "light",
        }

    if any(r in text for r in ["别攻击人", "别骂人", "别嘲讽", "别嘲讽我"]):
        return {
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人不要攻击或嘲讽他人。",
            "banter_level": "none",
        }

    if any(r in text for r in ["别引战", "别拱火", "别站队"]):
        return {
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人不要引战、拱火或站队。",
            "banter_level": "none",
        }

    if any(r in text for r in ["不舒服", "难受", "尴尬", "尴尬了"]):
        return {
            "kind": "negative_feedback",
            "score": 0.70,
            "content": "用户对机器人回复感到不舒服或尴尬，后续应降低调侃强度。",
            "banter_level": "light",
        }

    if any(r in text for r in ["生气", "不爽", "不高兴", "反感"]):
        return {
            "kind": "negative_feedback",
            "score": 0.75,
            "content": "用户对机器人回复表示不满，后续应收敛语气。",
            "banter_level": "none",
        }

    if any(r in text for r in ["过分了", "过分", "太过了", "有点过了"]):
        return {
            "kind": "negative_feedback",
            "score": 0.70,
            "content": "用户认为机器人调侃过度，后续应降低强度。",
            "banter_level": "light",
        }

    # 默认
    return {
        "kind": "preference",
        "score": 0.70,
        "content": "用户希望调整互动风格。",
        "banter_level": "light",
    }


def determine_banter_level(
    *,
    user_message: str = "",
    group_heat_state: str = "quiet",
    user_discomfort: bool = False,
    existing_boundaries: list[str] | None = None,
    is_roleplay_context: bool = False,
) -> str:
    """根据上下文确定调侃强度级别。

    Args:
        user_message: 用户当前消息
        group_heat_state: 群热度状态
        user_discomfort: 用户是否表达不满
        existing_boundaries: 已有的边界/偏好记忆
        is_roleplay_context: 是否为角色扮演上下文

    Returns:
        调侃强度级别："none" | "light" | "normal" | "high"
    """
    existing_boundaries = existing_boundaries or []
    # 1. 用户明确要求降低
    if detect_banter_boundary_request(user_message):
        preference = extract_banter_boundary_preference(user_message)
        return preference.get("banter_level", "light")

    # 2. 用户表达不满
    if user_discomfort:
        return "none"

    # 3. 已有边界记忆
    for boundary in existing_boundaries:
        boundary_lower = boundary.lower()
        if any(
            kw in boundary_lower for kw in ["别阴阳怪气", "别调侃", "认真点", "别攻击"]
        ):
            return "none"
        if any(kw in boundary_lower for kw in ["正常点", "别这样", "不舒服"]):
            return "light"

    # 4. 群聊高噪音时降低
    if group_heat_state in ("hot", "flood"):
        return "light"

    # 5. 角色扮演上下文
    if is_roleplay_context:
        return "light"  # 可以接梗，但不能升级为攻击

    # 6. 默认
    return "normal"


def build_social_boundary_rules() -> str:
    """构建社交边界安全规则的 prompt 文本。

    Returns:
        社交边界规则文本，用于加入系统 prompt
    """
    return """你可以幽默、自然、轻松，但必须遵守社交边界：

1. 不攻击用户本人，不评价用户人格、智商、外貌、家庭、身份。
2. 不使用羞辱性、贬低性、挑衅性语言。
3. 不主动引战，不挑起群成员矛盾，不站队拱火。
4. 不把玩笑升级成骂战。
5. 不对用户持续阴阳怪气。
6. 用户开玩笑时，可以轻微接梗，但不要过度调侃。
7. 如果用户表达不舒服、生气、尴尬、反感，应立即收敛语气，改为正常解释或道歉。
8. 对敏感话题、争议话题、群内矛盾，优先降温，不拱火。
9. 回复应该短、自然、有边界，不要说教。
10. 不说"你是不是有病""你真蠢""废物""滚""闭嘴"等攻击词汇。
11. 不说"笑死，急了""急了急了""破防"等过度阴阳怪气。
12. 群聊中不说"你们打一架""我站xxx"等拱火内容。"""
