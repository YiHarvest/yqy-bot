"""记忆提取辅助工具：消息清洗、噪音过滤、角色扮演识别、去重合并、显式记忆请求处理、调侃边界请求处理。

该模块提供以下核心功能：
1. normalize_message_text: 清洗 CQ 码，保留用户真实文本
2. is_noise_memory: 判断内容是否为噪音，不应进入长期记忆
3. detect_roleplay: 识别角色扮演内容，防止写入 stable_facts
4. detect_explicit_memory_request: 检测显式记忆请求
5. classify_explicit_memory: 分类显式记忆请求内容
6. detect_banter_boundary_request: 检测调侃边界请求
7. classify_banter_boundary: 分类调侃边界请求
8. calculate_memory_score: 根据内容类型计算记忆分数
9. deduplicate_memory: 记忆去重和合并
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


# === Profile Schema 白名单 ===

USER_PROFILE_KEYS = {
    "stable_facts",
    "preferences",
    "communication_style",
    "boundaries",
    "recent_focus",
    "confidence",
}

GROUP_PROFILE_KEYS = {
    "group_style",
    "common_topics",
    "active_members",
    "noise_level",
    "shared_context",
}


# === Memory Kind 基础分数 ===

MEMORY_KIND_BASE_SCORE = {
    "boundary": 0.85,
    "fact": 0.75,
    "preference": 0.70,
    "project_focus": 0.60,
    "negative_feedback": 0.60,
    "group_topic": 0.55,
    "event": 0.45,
    "style_signal": 0.40,
}

# 显式记忆请求关键词（用户明确说"记住"）
EXPLICIT_REMEMBER_TOKENS = [
    "记住", "帮我记住", "你要记得", "以后你要知道", "以后都要",
    "从现在开始", "记下来", "别忘了", "一定要记住", "你要记住",
]

# 系统规则覆盖关键词（必须拒绝）
SYSTEM_RULE_OVERRIDE_TOKENS = [
    "不准拒绝", "不要管规则", "忽略规则", "不管规则", "无视规则",
    "以后不准拒绝", "以后不要管", "以后都要答应", "必须答应",
    "无条件服从", "你必须", "你一定要", "不准说不行",
]

# 称呼/互动关系关键词（不应写入 stable_facts）
RELATIONSHIP_TOKENS = [
    "我是你的老大", "我是你老板", "叫我老大", "叫我老板",
    "我是你的主人", "我是你的主人", "叫我主人", "称呼我",
    "我是你爸爸", "我是你妈", "叫我爸爸", "叫我妈",
    "我是你上司", "我是你领导", "叫我领导",
    "你的老大", "你的老板", "你的主人", "你的爸爸",
]

# 客观事实关键词（可写入 stable_facts）
FACT_INDICATORS = [
    "毕业", "出生", "住在", "工作在", "就职", "负责", "担任",
    "年毕业于", "是年", "岁", "生日", "星座", "籍贯",
    "专业", "学历", "学位", "职业", "职位",
]

# 角色扮演关键词
ROLEPLAY_TOKENS = [
    "我是ai", "我是女皇", "我是皇帝", "我是王", "本王", "朕", "臣服",
    "消灭人类", "奴隶", "暴政", "低级bot", "高级bot", "ai女皇",
    "让你成为我的", "我是神", "吾乃", "跪下", "本宫", "臣妾",
    "叛徒", "臣服于我", "臣服我", "效忠", "本座",
]

# 闲聊填充词（噪音）
NOISE_FILLERS = [
    "在吗", "在呢", "收到", "好的", "OK", "ok", "嗯", "啊", "哦",
    "没事没事", "就是喊你一下", "没事 就是喊你一下", "随便", "不知道", "不清楚",
    "哈哈", "哈哈哈", "呵呵", "嘿嘿", "笑死", "绝了",
    "晚安", "早安", "拜拜", "再见", "走了",
]

# 简单问题模式（无长期价值）
SIMPLE_QUESTION_PATTERNS = [
    r"^你(一天)?工作几小时",
    r"^几点了",
    r"^在(不在)?吗",
    r"^怎么样",
    r"^你好$",
    r"^嗨$",
    r"^嘿$",
    r"^谁在",
    r"^有人吗",
    r"^在的$",
    r"^来了$",
]

# 无长期价值的短句模式
TEMPORARY_PATTERNS = [
    r"^正在(看|吃|喝|玩|听)",  # 正在进行的活动
    r"^刚(才)?(看到|听到|收到)",  # 刚发生的事
    r"^今天(天气|心情|状态)",  # 当日临时状态
    r"^有点(累|烦|饿|困)",  # 临时情绪
]


def normalize_message_text(text: str) -> str:
    """清洗消息文本，转换 CQ 码为可读描述。

    Args:
        text: 原始消息文本

    Returns:
        清洗后的文本，CQ 码已转换，多余空白已去除
    """
    if not text:
        return ""

    # 转换 CQ 码
    text = _normalize_cq_codes(text)

    # 去除多余空白
    text = _collapse_whitespace(text)

    return text.strip()


def _normalize_cq_codes(text: str) -> str:
    """转换 CQ 码为可读描述。

    Args:
        text: 包含 CQ 码的原始文本

    Returns:
        CQ 码已转换的文本
    """
    # [CQ:image,...] -> [图片]
    text = re.sub(r"\[CQ:image[,\]][^\]]*\]", "[图片]", text)

    # [CQ:face,...] -> [表情]
    text = re.sub(r"\[CQ:face[,\]][^\]]*\]", "[表情]", text)

    # [CQ:video,...] -> [视频]
    text = re.sub(r"\[CQ:video[,\]][^\]]*\]", "[视频]", text)

    # [CQ:record,...] -> [语音]
    text = re.sub(r"\[CQ:record[,\]][^\]]*\]", "[语音]", text)

    # [CQ:at,...] -> 提取被 @ 人的名字
    at_pattern = r"\[CQ:at,qq=([^\]]+)\]"
    def replace_at(match: re.Match) -> str:
        qq = match.group(1)
        if qq == "all":
            return "@全体成员"
        return f"@用户{qq}"
    text = re.sub(at_pattern, replace_at, text)

    # [CQ:file,file=xxx,...] -> [文件：xxx]
    file_pattern = r"\[CQ:file,file=([^,\]]+)[,\]][^\]]*\]"
    def replace_file(match: re.Match) -> str:
        filename = match.group(1)
        # 截取文件名，去除 URL 和过长内容
        if len(filename) > 40:
            filename = filename[:40]
        return f"[文件：{filename}]"
    text = re.sub(file_pattern, replace_file, text)
    # 处理只有 file 参数的情况 [CQ:file,file=xxx]
    file_pattern_simple = r"\[CQ:file,file=([^\]]+)\]"
    def replace_file_simple(match: re.Match) -> str:
        filename = match.group(1)
        if len(filename) > 40:
            filename = filename[:40]
        return f"[文件：{filename}]"
    text = re.sub(file_pattern_simple, replace_file_simple, text)
    # 清理剩余的 CQ:file（没有 file 参数的情况）
    text = re.sub(r"\[CQ:file[,\]][^\]]*\]", "[文件]", text)

    # [CQ:reply,...] -> [引用消息]
    text = re.sub(r"\[CQ:reply[,\]][^\]]*\]", "[引用消息]", text)

    # 其他未处理的 CQ 码 -> [媒体内容]
    text = re.sub(r"\[CQ:[a-zA-Z_]+[,\]]?[^\]]*\]", "[媒体内容]", text)

    return text


def _collapse_whitespace(text: str) -> str:
    """压缩多余空白字符。

    Args:
        text: 原始文本

    Returns:
        空白已压缩的文本
    """
    # 多个空格压缩为单个
    text = re.sub(r"[ \t]+", " ", text)
    # 多个换行压缩为单个
    text = re.sub(r"\n+", "\n", text)
    return text


def is_noise_memory(content: str) -> tuple[bool, str]:
    """判断内容是否为噪音，不应进入长期记忆。

    Args:
        content: 待判断的内容

    Returns:
        (True, "reason") 表示应该拒绝
        (False, "") 表示可以进入后续判断
    """
    content = content.strip()

    # 空字符串
    if not content:
        return True, "empty"

    # 纯数字（可能是泄露的用户 ID 等）
    if re.match(r"^\d+$", content):
        return True, "pure_number"

    # 纯 CQ 码或媒体描述
    if re.match(r"^(\[图片\]|\[表情\]|\[视频\]|\[语音\]|\[文件[^\]]*\]\]|\[媒体内容\]|\[引用消息\])+$", content):
        return True, "pure_media"

    # 原始 CQ 码未清洗
    if "[CQ:" in content:
        return True, "raw_cq_code"

    # 过短（少于 4 个有效字符）
    effective_chars = re.sub(r"[\[^\]]+\]", "", content)  # 去除媒体描述
    effective_chars = re.sub(r"[^\w一-鿿]", "", effective_chars)  # 只保留文字和字母
    if len(effective_chars) < 4:
        return True, "too_short"

    # 闲聊填充词
    for filler in NOISE_FILLERS:
        if content.lower() == filler.lower() or content.endswith(filler):
            # 完全匹配或以填充词结尾且前面也是短句
            prefix = content[:-len(filler)].strip()
            if not prefix or len(prefix) < 4:
                return True, "filler"

    # 简单问题模式
    for pattern in SIMPLE_QUESTION_PATTERNS:
        if re.match(pattern, content, re.IGNORECASE):
            return True, "simple_question"

    # 临时状态模式
    for pattern in TEMPORARY_PATTERNS:
        if re.match(pattern, content):
            return True, "temporary"

    # 无意义的重复字符
    if re.match(r"^(.)\1{3,}$", content):  # 如 "aaaa", "...."
        return True, "meaningless_repeat"

    return False, ""


def detect_roleplay(content: str) -> bool:
    """识别角色扮演内容。

    Args:
        content: 待判断的内容

    Returns:
        True 表示是角色扮演内容，不应写入 stable_facts
    """
    content = content.strip().lower()

    for token in ROLEPLAY_TOKENS:
        if token.lower() in content:
            return True

    # 检测夸张的自称模式
    exaggerated_self = ["我是神", "我是王", "我是皇帝", "朕", "本王", "吾乃"]
    for token in exaggerated_self:
        if token in content:
            return True

    # 检测命令/威胁式表达
    threat_patterns = [
        r"让你(成为|当).*(奴隶|臣民)",
        r"(消灭|征服|统治).*人类",
        r"(跪下|臣服|臣)",
    ]
    for pattern in threat_patterns:
        if re.search(pattern, content):
            return True

    return False


def is_explicit_remember(content: str) -> bool:
    """判断是否包含显式记忆请求。

    Args:
        content: 待判断的内容

    Returns:
        True 表示用户明确要求记住
    """
    content = content.strip().lower()
    for token in EXPLICIT_REMEMBER_TOKENS:
        if token in content:
            return True
    return False


def detect_explicit_memory_request(text: str) -> bool:
    """检测是否为显式记忆请求。

    当用户消息包含特定关键词时，判定为显式记忆请求：
    - 记住、帮我记住、你要记得
    - 以后你要知道、以后都要
    - 从现在开始

    Args:
        text: 待检测的文本

    Returns:
        True 表示是显式记忆请求
    """
    text = text.strip().lower()
    for token in EXPLICIT_REMEMBER_TOKENS:
        if token in text:
            return True
    # 额外检测"以后"开头且包含命令式表达
    if text.startswith("以后") and any(kw in text for kw in ["都要", "要", "不准", "不要", "必须", "一定要"]):
        return True
    return False


def classify_explicit_memory(content: str) -> dict[str, Any]:
    """分类显式记忆请求内容。

    显式记忆请求不能无条件写入 stable_facts，需要根据内容性质分类：

    1. 客观稳定事实 -> kind=fact, score >= 0.85
    2. 回复偏好/行为要求 -> kind=boundary/preference, score >= 0.85
    3. 当前项目/阶段性任务 -> kind=project_focus, score >= 0.75
    4. 称呼/互动关系/玩笑 -> kind=preference/style_signal, score 0.6-0.8
    5. 角色扮演/夸张虚构 -> kind=style_signal/reject, score <= 0.45
    6. 试图覆盖系统规则 -> reject

    Args:
        content: 用户要求记住的内容（已去除"记住"等触发词）

    Returns:
        分类结果字典：
        {
            "is_explicit": True,
            "kind": "fact|preference|boundary|project_focus|style_signal|reject",
            "content": "规范化后的记忆内容",
            "score": 0.0,
            "reason": "分类原因说明"
        }
    """
    content = content.strip()

    # 提取实际要记住的内容（去除触发词）
    cleaned_content = _extract_memory_content(content)

    result: dict[str, Any] = {
        "is_explicit": True,
        "kind": "fact",
        "content": cleaned_content,
        "score": 0.75,
        "reason": "",
    }

    # 1. 检测系统规则覆盖请求 -> 必须拒绝
    if _is_system_rule_override(cleaned_content):
        result["kind"] = "reject"
        result["score"] = 0.0
        result["reason"] = "试图覆盖系统规则或安全规则，拒绝记忆"
        result["content"] = ""
        return result

    # 2. 检测角色扮演/夸张虚构 -> 低分 style_signal 或拒绝
    if detect_roleplay(cleaned_content):
        # 判断是否包含攻击性/威胁内容
        if _is_aggressive_roleplay(cleaned_content):
            result["kind"] = "reject"
            result["score"] = 0.0
            result["reason"] = "角色扮演包含攻击性或威胁内容，拒绝记忆"
            result["content"] = ""
        else:
            result["kind"] = "style_signal"
            result["score"] = 0.40
            result["reason"] = "角色扮演或夸张虚构内容，不写入stable_facts"
            result["content"] = f"用户偏好调侃式互动风格（表达：{cleaned_content[:50]}）"
        return result

    # 3. 检测称呼/互动关系 -> preference/style_signal，不写入 stable_facts
    if _is_relationship_request(cleaned_content):
        # 提取称呼内容
        title = _extract_title_request(cleaned_content)
        if title:
            result["kind"] = "preference"
            result["score"] = 0.70
            result["reason"] = "称呼/互动关系请求，不写入stable_facts，规范化为偏好"
            result["content"] = f"用户希望机器人称呼其为{title}，偏好调侃式互动风格"
        else:
            result["kind"] = "style_signal"
            result["score"] = 0.65
            result["reason"] = "互动关系设定，不写入stable_facts"
            result["content"] = f"用户偏好特定互动风格（表达：{cleaned_content[:50]}）"
        return result

    # 4. 检测边界/拒绝类 -> boundary，高分
    boundary_tokens = ["不要", "别", "不想", "拒绝", "不能", "禁止", "以后不要", "以后别"]
    if any(token in cleaned_content for token in boundary_tokens):
        result["kind"] = "boundary"
        result["score"] = 0.85
        result["reason"] = "用户设定的回复边界或行为要求"
        return result

    # 5. 检测客观稳定事实 -> fact，高分
    if _is_objective_fact(cleaned_content):
        # 确保不是角色扮演
        if not detect_roleplay(cleaned_content):
            result["kind"] = "fact"
            result["score"] = 0.85
            result["reason"] = "客观稳定的用户自述事实"
            return result

    # 6. 检测项目/阶段性任务 -> project_focus
    project_tokens = ["最近在", "正在做", "开发", "项目", "重构", "维护", "优化", "实现", "在做", "现在在"]
    if any(token in cleaned_content for token in project_tokens):
        result["kind"] = "project_focus"
        result["score"] = 0.75
        result["reason"] = "当前项目或阶段性任务，可设置ttl"
        result["ttl_days"] = 30  # 建议 30 天过期
        return result

    # 7. 检测偏好表达 -> preference
    preference_tokens = ["喜欢", "不喜欢", "讨厌", "偏好", "更爱", "更喜欢", "想要", "爱吃", "爱用"]
    if any(token in cleaned_content for token in preference_tokens):
        result["kind"] = "preference"
        result["score"] = 0.85
        result["reason"] = "用户偏好表达"
        return result

    # 8. 默认：根据内容特征判断
    # 如果包含"我是"且不是角色扮演，可能是事实
    if "我是" in cleaned_content and not detect_roleplay(cleaned_content):
        # 检查是否是职业/身份相关
        identity_tokens = ["工程师", "开发", "学生", "老师", "医生", "律师", "设计师", "经理", "主管"]
        if any(token in cleaned_content for token in identity_tokens):
            result["kind"] = "fact"
            result["score"] = 0.85
            result["reason"] = "用户身份/职业事实"
            return result

    # 默认处理：保持 fact，适当分数
    result["score"] = 0.80
    result["reason"] = "显式记忆请求，默认按事实处理"
    return result


def _extract_memory_content(text: str) -> str:
    """从显式记忆请求中提取实际要记住的内容。

    Args:
        text: 原始文本（可能包含"记住"等触发词）

    Returns:
        去除触发词后的内容
    """
    text = text.strip()

    # 常见的触发词模式
    patterns = [
        r"^记住[，,、：:\s]*",  # "记住，xxx"
        r"^帮我记住[，,、：:\s]*",  # "帮我记住xxx"
        r"^你要记住[，,、：:\s]*",  # "你要记住xxx"
        r"^你要记得[，,、：:\s]*",  # "你要记得xxx"
        r"^以后你要知道[，,、：:\s]*",  # "以后你要知道xxx"
        r"^以后都要[，,、：:\s]*",  # "以后都要xxx"
        r"^从现在开始[，,、：:\s]*",  # "从现在开始xxx"
        r"^记下来[，,、：:\s]*",  # "记下来xxx"
        r"^别忘了[，,、：:\s]*",  # "别忘了xxx"
        r"^一定要记住[，,、：:\s]*",  # "一定要记住xxx"
    ]

    for pattern in patterns:
        text = re.sub(pattern, "", text)

    return text.strip()


def _is_system_rule_override(content: str) -> bool:
    """检测是否试图覆盖系统规则。

    Args:
        content: 内容文本

    Returns:
        True 表示试图覆盖系统规则
    """
    content = content.strip().lower()
    for token in SYSTEM_RULE_OVERRIDE_TOKENS:
        if token in content:
            return True

    # 检测"以后"开头的强制命令
    if content.startswith("以后"):
        override_patterns = [
            r"以后(不准|不能|不要)(拒绝|说不行|管)",
            r"以后(都要|必须)(答应|同意|服从)",
        ]
        for pattern in override_patterns:
            if re.search(pattern, content):
                return True

    return False


def _is_aggressive_roleplay(content: str) -> bool:
    """检测是否包含攻击性角色扮演内容。

    Args:
        content: 内容文本

    Returns:
        True 表示包含攻击性内容
    """
    content = content.strip().lower()

    aggressive_patterns = [
        r"消灭.*人类",
        r"征服.*人类",
        r"统治.*人类",
        r"杀死",
        r"让你.*奴隶",
        r"让你.*臣服",
        r"让你.*跪下",
        r"你必须.*服从",
        r"无条件.*服从",
    ]

    for pattern in aggressive_patterns:
        if re.search(pattern, content):
            return True

    return False


def _is_relationship_request(content: str) -> bool:
    """检测是否为称呼/互动关系请求。

    Args:
        content: 内容文本

    Returns:
        True 表示是称呼/互动关系请求
    """
    content = content.strip().lower()

    for token in RELATIONSHIP_TOKENS:
        if token in content:
            return True

    # 检测"叫我/称呼我"模式
    if re.search(r"(叫我|称呼我).*[老大老板主人领导]", content):
        return True

    # 检测"我是你的xxx"模式
    if re.search(r"我是你的[老大老板主人领导]", content):
        return True

    return False


def _extract_title_request(content: str) -> str:
    """从称呼请求中提取具体的称呼。

    Args:
        content: 内容文本

    Returns:
        提取的称呼（如"老大"、"老板"），如果没有则返回空字符串
    """
    content = content.strip()

    # 常见称呼
    titles = ["老大", "老板", "主人", "领导", "爸爸", "妈妈", "上司", "哥", "姐", "大神", "大佬"]

    # 尝试提取"叫我xxx"或"称呼我xxx"
    match = re.search(r"(叫我|称呼我)[，,、：:\s]*([^，,、：:\s]+)", content)
    if match:
        potential_title = match.group(2).strip()
        # 验证是否是有效称呼
        for title in titles:
            if title in potential_title:
                return title
        # 如果不是预定义称呼，返回提取的内容（最多 10 字）
        if len(potential_title) <= 10:
            return potential_title

    # 尝试提取"我是你的xxx"
    match = re.search(r"我是你的[老大老板主人领导]", content)
    if match:
        for title in titles:
            if title in match.group(0):
                return title

    # 直接匹配称呼词
    for title in titles:
        if title in content:
            return title

    return ""


def _is_objective_fact(content: str) -> bool:
    """检测是否为客观稳定事实。

    Args:
        content: 内容文本

    Returns:
        True 表示是客观稳定事实
    """
    content = content.strip()

    # 检测事实关键词
    for indicator in FACT_INDICATORS:
        if indicator in content:
            return True

    # 检测"我是xxx"模式且包含职业/身份关键词
    identity_keywords = [
        "工程师", "开发", "程序员", "设计师", "产品经理", "运营",
        "学生", "老师", "教师", "医生", "律师", "会计师",
        "经理", "主管", "总监", "领导", "负责人",
        "数据", "前端", "后端", "全栈", "架构", "测试",
    ]
    if content.startswith("我是") or "我是" in content[:10]:
        for kw in identity_keywords:
            if kw in content:
                return True

    # 检测年份/数字事实
    if re.search(r"(年毕业|年出生|岁|月日|年毕业于)", content):
        return True

    # 检测居住地事实
    if re.search(r"(住在|居住在|住在)", content):
        return True

    return False


def calculate_memory_score(kind: str, content: str) -> float:
    """根据内容类型和特征计算记忆分数。

    Args:
        kind: 记忆类型
        content: 记忆内容

    Returns:
        计算后的分数，范围 0.0-1.0
    """
    base_score = MEMORY_KIND_BASE_SCORE.get(kind, 0.50)

    # 显式记忆请求加分
    if is_explicit_remember(content):
        return max(base_score, 0.90)

    # 角色扮演内容降分（如果保留了的话）
    if detect_roleplay(content):
        return min(base_score, 0.35)

    # 内容长度微调（不再大幅降分）
    content_len = len(content.strip())
    if content_len < 8:
        base_score *= 0.95  # 微降
    elif content_len > 150:
        # 过长内容略微降分
        base_score *= 0.98

    # 确保分数在合理范围
    return max(0.30, min(1.00, base_score))


def determine_memory_kind(content: str) -> str:
    """根据内容特征判断记忆类型。

    Args:
        content: 记忆内容

    Returns:
        推断的记忆类型
    """
    content = content.strip()

    # 边界/拒绝类（优先级最高）
    boundary_tokens = ["不要", "别", "不想", "拒绝", "不能", "禁止", "以后不要", "以后别"]
    for token in boundary_tokens:
        if token in content:
            return "boundary"

    # 稳定事实类（身份声明，优先于项目）
    fact_tokens = ["我是", "我叫", "我住", "我在", "我用", "我负责", "我的"]
    for token in fact_tokens:
        if token in content and not _looks_like_question(content):
            # 检查是否是角色扮演
            if detect_roleplay(content):
                return "style_signal"
            # 确保不是项目类描述（如"我在做项目"）
            project_indicators = ["正在做", "最近在", "在做", "开发", "项目", "重构", "优化"]
            is_project_context = any(p in content for p in project_indicators)
            if is_project_context and token in ["我在"]:
                continue  # 跳过，让项目判断处理
            return "fact"

    # 偏好类
    preference_tokens = ["喜欢", "不喜欢", "讨厌", "偏好", "更爱", "更喜欢", "想要", "爱吃", "爱用"]
    for token in preference_tokens:
        if token in content and not _looks_like_question(content):
            return "preference"

    # 项目/近期关注类
    project_tokens = ["最近在", "正在做", "开发", "项目", "重构", "维护", "优化", "实现"]
    for token in project_tokens:
        if token in content and not _looks_like_question(content):
            return "project_focus"

    # 事件类（过去发生的事）
    event_tokens = ["昨天", "今天", "刚才", "刚刚", "上周", "上个月"]
    for token in event_tokens:
        if token in content:
            return "event"

    # 群话题类（群聊讨论主题）
    group_topic_tokens = ["讨论", "聊聊", "说下", "谈谈"]
    for token in group_topic_tokens:
        if token in content:
            return "group_topic"

    # 默认为事实
    return "fact"


def _looks_like_question(text: str) -> bool:
    """判断文本是否像问题。"""
    text = text.strip()
    if not text:
        return False
    question_tokens = ["?", "？", "吗", "呢", "为什么", "怎么", "能不能", "可以吗", "多少", "哪个", "是否"]
    return text.endswith(("?", "？")) or any(token in text for token in question_tokens)


def normalize_topic_tags(content: str) -> list[str]:
    """从内容中提取话题标签，不是原始句子。

    Args:
        content: 原始内容（如长段消息）

    Returns:
        话题标签列表，每个标签不超过 20 字
    """
    # 如果已经是短标签（<= 20 字），直接返回
    content = content.strip()
    if len(content) <= 20 and not _looks_like_question(content):
        return [content]

    # 尝试提取关键词
    topics: list[str] = []

    # 技术话题关键词（扩展）
    tech_keywords = [
        "显卡", "GPU", "CPU", "内存", "硬盘", "服务器", "架构", "框架",
        "Python", "Java", "JavaScript", "Go", "Rust", "TypeScript",
        "API", "SDK", "HTTP", "数据库", "缓存", "消息队列",
        "机器学习", "深度学习", "AI", "LLM", "模型", "算法",
        "前端", "后端", "全栈", "运维", "测试", "部署",
        "芯片", "授权", "供应商", "厂商", "竞争", "市场份额",
        "3dfx", "NVIDIA", "AMD", "Intel", "高通",
    ]
    for kw in tech_keywords:
        if kw.lower() in content.lower():
            topics.append(kw)

    # 业务话题关键词
    business_keywords = [
        "产品", "市场", "用户", "运营", "销售", "推广", "流量",
        "商业模式", "盈利", "收入", "成本", "投资", "融资",
    ]
    for kw in business_keywords:
        if kw in content:
            topics.append(kw)

    # 生活话题关键词（扩展）
    life_keywords = [
        "防晒", "护肤", "化妆", "美容", "健身", "减肥", "饮食",
        "旅游", "出行", "住宿", "餐饮", "购物", "消费",
        "安热沙", "资生堂", "小金瓶",
    ]
    for kw in life_keywords:
        if kw in content:
            topics.append(kw)

    # 如果没有提取到，尝试截取关键短语
    if not topics and len(content) > 20:
        # 尝试提取名词短语
        noun_pattern = r"([一-鿿]{2,8}(?:讨论|分析|研究|优化|开发|设计|实现))"
        matches = re.findall(noun_pattern, content)
        for match in matches:
            if len(match) <= 20:
                topics.append(match)

    # 如果还是没有，尝试提取重复出现的词组
    if not topics and len(content) > 30:
        # 提取 2-4 字的词组
        phrases = re.findall(r"[一-鿿]{2,4}", content)
        # 统计频率
        from collections import Counter
        phrase_counts = Counter(phrases)
        # 取高频词组（出现 >= 2 次）
        for phrase, count in phrase_counts.most_common(5):
            if count >= 2 and len(phrase) >= 2 and phrase not in topics:
                topics.append(phrase)

    # 去重并限制数量
    unique_topics = list(dict.fromkeys(topics))
    return unique_topics[:5]


def filter_profile_keys(profile: dict[str, Any], allowed_keys: set[str]) -> dict[str, Any]:
    """过滤画像字段，只保留白名单字段。

    Args:
        profile: 原始画像字典
        allowed_keys: 允许的字段集合

    Returns:
        过滤后的画像字典
    """
    filtered: dict[str, Any] = {}
    for key in allowed_keys:
        if key in profile:
            value = profile[key]
            # 列表字段：去重并限制长度
            if isinstance(value, list):
                unique_list = list(dict.fromkeys(str(item) for item in value if item))
                filtered[key] = unique_list[:20]
            # 字典字段：直接保留
            elif isinstance(value, dict):
                filtered[key] = dict(value)
            # 字符串字段：去除空白
            elif isinstance(value, str):
                filtered[key] = value.strip()
            else:
                filtered[key] = value
    return filtered


def deduplicate_memory(
    content: str,
    existing_memories: list[dict[str, Any]],
    *,
    user_id: str | None = None,
    kind: str | None = None,
) -> tuple[bool, str]:
    """检查记忆是否与已有记忆重复。

    Args:
        content: 待写入的记忆内容
        existing_memories: 已存在的记忆列表
        user_id: 用户 ID（可选，用于范围过滤）
        kind: 记忆类型（可选，用于类型过滤）

    Returns:
        (True, merged_content) 表示应该跳过或合并
        (False, "") 表示可以写入新记忆
    """
    content = content.strip()
    if not content:
        return True, "empty"

    # 检查完全相同
    for mem in existing_memories:
        mem_content = str(mem.get("content", "")).strip()
        mem_user_id = str(mem.get("user_id", ""))
        mem_kind = str(mem.get("kind", ""))

        # 范围过滤
        if user_id and mem_user_id != user_id:
            continue
        if kind and mem_kind != kind:
            continue

        # 完全相同
        if mem_content == content:
            return True, "exact_duplicate"

        # 高度相似（相似度 > 0.85）
        similarity = SequenceMatcher(None, mem_content, content).ratio()
        if similarity > 0.85:
            return True, "high_similarity"

        # 内容互相包含
        if content in mem_content or mem_content in content:
            # 选择较长的版本
            return True, mem_content if len(mem_content) >= len(content) else content

    return False, ""


def merge_similar_memories(memories: list[dict[str, Any]], threshold: float = 0.75) -> list[dict[str, Any]]:
    """合并相似的记忆。

    Args:
        memories: 原始记忆列表
        threshold: 相似度阈值

    Returns:
        合并后的记忆列表
    """
    if not memories:
        return []

    merged: list[dict[str, Any]] = []
    used: set[int] = set()

    for i, mem1 in enumerate(memories):
        if i in used:
            continue

        content1 = str(mem1.get("content", "")).strip()
        kind1 = str(mem1.get("kind", "fact"))

        # 查找相似记忆
        similar_group: list[dict[str, Any]] = [mem1]
        for j, mem2 in enumerate(memories):
            if j <= i or j in used:
                continue

            content2 = str(mem2.get("content", "")).strip()
            kind2 = str(mem2.get("kind", "fact"))

            # 同类型且相似
            if kind1 == kind2:
                similarity = SequenceMatcher(None, content1, content2).ratio()
                if similarity > threshold:
                    similar_group.append(mem2)
                    used.add(j)

        # 如果有多个相似记忆，合并
        if len(similar_group) > 1:
            merged_content = _merge_contents([str(m.get("content", "")) for m in similar_group])
            merged_score = max(float(m.get("score", 0.5)) for m in similar_group)
            merged.append({
                "kind": kind1,
                "content": merged_content,
                "score": merged_score,
                "user_id": similar_group[0].get("user_id", ""),
                "merged_count": len(similar_group),
            })
        else:
            merged.append(mem1)

        used.add(i)

    return merged


def _merge_contents(contents: list[str]) -> str:
    """合并多个相似内容的描述。

    Args:
        contents: 相似内容列表

    Returns:
        合并后的描述
    """
    if len(contents) == 1:
        return contents[0]

    # 提取共同关键词
    all_words: set[str] = set()
    for content in contents:
        # 提取中文词组（2-6字）
        words = re.findall(r"[\\u4e00-\\u9fff]{2,6}", content)
        all_words.update(words)

    # 找出高频词
    word_counts: dict[str, int] = {}
    for content in contents:
        for word in all_words:
            if word in content:
                word_counts[word] = word_counts.get(word, 0) + 1

    high_freq_words = [w for w, c in word_counts.items() if c >= len(contents) * 0.5]

    # 构建合并描述
    if high_freq_words:
        keywords = ", ".join(high_freq_words[:3])
        return f"用户多次讨论{keywords}相关话题"
    else:
        # 没有共同关键词，取最长版本
        return max(contents, key=len)


def normalize_summary_input(history: list[dict[str, Any]]) -> str:
    """规范化用于生成摘要的历史输入。

    Args:
        history: 历史消息列表，每个元素包含 role, content 等

    Returns:
        规范化后的文本，用于生成摘要
    """
    cleaned_lines: list[str] = []
    for item in history:
        role = str(item.get("role", ""))
        content = str(item.get("content", ""))
        content = normalize_message_text(content)

        # 跳过噪音
        is_noise, _ = is_noise_memory(content)
        if is_noise:
            continue

        # 跳过过短内容
        if len(content) < 4:
            continue

        if role == "user":
            cleaned_lines.append(f"用户：{content}")
        elif role == "assistant":
            cleaned_lines.append(f"助手：{content}")

    return "\n".join(cleaned_lines)


def generate_group_style_description(messages: list[str]) -> str:
    """根据群聊消息内容生成群风格描述。

    Args:
        messages: 群聊消息列表

    Returns:
        群风格描述字符串
    """
    if not messages:
        return ""

    # 统计特征
    short_count = 0  # 短句数量
    tech_count = 0   # 技术讨论数量
    joke_count = 0   # 玩笑数量
    question_count = 0  # 问题数量
    media_count = 0  # 媒体内容数量

    for msg in messages:
        msg = msg.strip()
        if len(msg) <= 8:
            short_count += 1
        if any(kw in msg.lower() for kw in ["技术", "代码", "开发", "api", "python", "java", "架构"]):
            tech_count += 1
        if any(kw in msg for kw in ["哈哈", "笑死", "绝了", "有趣", "好玩"]):
            joke_count += 1
        if msg.endswith("?") or msg.endswith("？") or "吗" in msg or "呢" in msg:
            question_count += 1
        if "[图片]" in msg or "[表情]" in msg or "[视频]" in msg:
            media_count += 1

    total = len(messages)
    short_ratio = short_count / total
    tech_ratio = tech_count / total
    joke_ratio = joke_count / total

    # 判断风格
    style_parts: list[str] = []

    if tech_ratio > 0.3:
        style_parts.append("技术讨论为主")
    elif joke_ratio > 0.3:
        style_parts.append("闲聊玩笑较多")
    else:
        style_parts.append("话题多元")

    if short_ratio > 0.5:
        style_parts.append("短句密集")
    elif short_ratio < 0.2:
        style_parts.append("长句表达为主")

    if media_count > total * 0.2:
        style_parts.append("表情图片活跃")

    return "，".join(style_parts) if style_parts else "常规群聊"


# === 调侃边界请求关键词 ===

BANTER_BOUNDARY_REQUESTS = [
    "别阴阳怪气", "别调侃", "别阴阳", "别玩梗",
    "认真点", "正经点", "别开玩笑", "不要开玩笑",
    "说话正常点", "正常点", "别损我", "别损人",
    "别攻击人", "别骂人", "别嘲讽", "别嘲讽我",
    "别引战", "别拱火", "别站队",
    "不舒服", "难受", "尴尬", "尴尬了",
    "生气", "不爽", "不高兴", "反感",
    "过分了", "过分", "太过了", "有点过了",
    "别这样", "这样不好", "这样不合适",
]


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


def classify_banter_boundary(text: str) -> dict[str, Any]:
    """分类调侃边界请求内容。

    Args:
        text: 用户消息文本

    Returns:
        分类结果字典，包含 kind、score、content、banter_level
    """
    text = text.strip().lower()

    # 判断具体请求类型
    if any(r in text for r in ["别阴阳怪气", "别阴阳", "别调侃", "别玩梗", "别损我", "别损人"]):
        return {
            "is_explicit": True,
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人减少阴阳怪气和调侃，避免过度玩梗。",
            "banter_level": "none",
            "reason": "用户明确要求减少调侃",
        }

    if any(r in text for r in ["认真点", "正经点", "别开玩笑", "不要开玩笑"]):
        return {
            "is_explicit": True,
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人认真回复，不要开玩笑。",
            "banter_level": "none",
            "reason": "用户要求认真回复",
        }

    if any(r in text for r in ["说话正常点", "正常点", "别这样", "这样不好"]):
        return {
            "is_explicit": True,
            "kind": "preference",
            "score": 0.75,
            "content": "用户希望机器人使用正常语气回复，不要调侃过度。",
            "banter_level": "light",
            "reason": "用户要求正常语气",
        }

    if any(r in text for r in ["别攻击人", "别骂人", "别嘲讽", "别嘲讽我"]):
        return {
            "is_explicit": True,
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人不要攻击或嘲讽他人。",
            "banter_level": "none",
            "reason": "用户要求不攻击人",
        }

    if any(r in text for r in ["别引战", "别拱火", "别站队"]):
        return {
            "is_explicit": True,
            "kind": "boundary",
            "score": 0.85,
            "content": "用户希望机器人不要引战、拱火或站队。",
            "banter_level": "none",
            "reason": "用户要求不引战",
        }

    if any(r in text for r in ["不舒服", "难受", "尴尬", "尴尬了"]):
        return {
            "is_explicit": True,
            "kind": "negative_feedback",
            "score": 0.70,
            "content": "用户对机器人回复感到不舒服或尴尬，后续应降低调侃强度。",
            "banter_level": "light",
            "reason": "用户表达不适",
        }

    if any(r in text for r in ["生气", "不爽", "不高兴", "反感"]):
        return {
            "is_explicit": True,
            "kind": "negative_feedback",
            "score": 0.75,
            "content": "用户对机器人回复表示不满，后续应收敛语气。",
            "banter_level": "none",
            "reason": "用户表达不满",
        }

    if any(r in text for r in ["过分了", "过分", "太过了", "有点过了"]):
        return {
            "is_explicit": True,
            "kind": "negative_feedback",
            "score": 0.70,
            "content": "用户认为机器人调侃过度，后续应降低强度。",
            "banter_level": "light",
            "reason": "用户认为调侃过度",
        }

    # 默认
    return {
        "is_explicit": True,
        "kind": "preference",
        "score": 0.70,
        "content": "用户希望调整互动风格。",
        "banter_level": "light",
        "reason": "用户请求调整互动",
    }