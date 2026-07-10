from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

DEFAULT_GROUP_TRIGGER_KEYWORDS = ["YHarvest", "机器人"]
DEFAULT_PERSONA_TONE = ["自然口语化", "短句为主", "不硬拗梗"]
DEFAULT_PERSONA_RULES = ["不要输出 CQ 码", "需要表情时要发送表情包"]
DEFAULT_LOW_INFO_SKIP_KEYWORDS = [
    "哈哈",
    "哈哈哈",
    "在吗",
    "晚安",
    "早安",
    "收到",
    "OK",
    "ok",
    "好的",
]
DEFAULT_QUESTION_TOKENS = ["?", "？", "吗", "呢"]

# 搜索触发关键词
DEFAULT_SEARCH_TRIGGER_KEYWORDS = [
    "搜一下",
    "查一下",
    "搜索",
    "帮我查",
    "帮我找",
    "联网查",
    "最新",
    "现在",
    "今天",
    "今年",
    "新闻",
    "价格",
    "版本",
    "官网",
    "文档",
    "PyPI",
    "GitHub",
    "release",
    "issue",
    "报错",
]


@dataclass(slots=True)
class SearchMCPSettings:
    """搜索 MCP 工具配置。"""

    enabled: bool = False
    command: str = "search-engine-tool-mcp"
    provider: str = "auto"
    max_results: int = 5
    timeout_seconds: float = 25.0

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "SearchMCPSettings":
        """从字典对象创建 SearchMCPSettings 实例。

        环境变量优先级高于配置文件。

        Args:
            payload: 配置字典，可选

        Returns:
            SearchMCPSettings 实例
        """
        data = dict(payload or {})
        env_enabled = os.getenv("SEARCH_MCP_ENABLED", "")
        # 使用字段默认值
        default_enabled = False
        default_command = "search-engine-tool-mcp"
        default_provider = "auto"
        default_max_results = 5
        default_timeout = 25.0
        return cls(
            enabled=bool(
                env_enabled.lower() in ("true", "1", "yes")
                if env_enabled
                else data.get("enabled", default_enabled)
            ),
            command=str(
                os.getenv("SEARCH_MCP_COMMAND") or data.get("command", default_command)
            ),
            provider=str(
                os.getenv("SEARCH_MCP_PROVIDER")
                or data.get("provider", default_provider)
            ),
            max_results=int(
                os.getenv("SEARCH_MCP_MAX_RESULTS")
                or data.get("max_results", default_max_results)
            ),
            timeout_seconds=float(
                os.getenv("SEARCH_MCP_TIMEOUT_SECONDS")
                or data.get("timeout_seconds", default_timeout)
            ),
        )


@dataclass(slots=True)
class NapCatSettings:
    http_base_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    timeout_seconds: float = 8.0
    custom_face_count: int = 48

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "NapCatSettings":
        """从字典对象创建 NapCatSettings 实例。

        Args:
            payload: 配置字典，可选

        Returns:
            NapCatSettings 实例，环境变量优先于字典值
        """
        data = dict(payload or {})
        env_access_token = os.getenv("NAPCAT_ACCESS_TOKEN") or ""
        return cls(
            http_base_url=str(
                os.getenv("NAPCAT_HTTP_BASE_URL")
                or data.get("http_base_url", cls.http_base_url)
            ),
            access_token=str(
                env_access_token or data.get("access_token", cls.access_token)
            ),
            timeout_seconds=float(
                os.getenv("NAPCAT_TIMEOUT_SECONDS")
                or data.get("timeout_seconds", cls.timeout_seconds)
            ),
            custom_face_count=int(data.get("custom_face_count", cls.custom_face_count)),
        )


@dataclass(slots=True)
class BotContextSettings:
    enable_history_backfill: bool = False
    history_backfill_cooldown_seconds: int = 60
    group_recent_turns_min: int = 5
    private_recent_turns_limit: int = 12
    group_recent_turns_limit: int = 20
    memory_limit: int = 5
    reflection_limit: int = 2
    prompt_max_chars: int = 8000

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "BotContextSettings":
        """从字典对象创建 BotContextSettings 实例。

        Args:
            payload: 配置字典，可选

        Returns:
            BotContextSettings 实例，缺失字段使用默认值填充
        """
        data = dict(payload or {})
        return cls(
            enable_history_backfill=bool(
                data.get("enable_history_backfill", cls.enable_history_backfill)
            ),
            history_backfill_cooldown_seconds=int(
                data.get(
                    "history_backfill_cooldown_seconds",
                    cls.history_backfill_cooldown_seconds,
                )
            ),
            group_recent_turns_min=int(
                data.get("group_recent_turns_min", cls.group_recent_turns_min)
            ),
            private_recent_turns_limit=int(
                data.get("private_recent_turns_limit", cls.private_recent_turns_limit)
            ),
            group_recent_turns_limit=int(
                data.get("group_recent_turns_limit", cls.group_recent_turns_limit)
            ),
            memory_limit=int(data.get("memory_limit", cls.memory_limit)),
            reflection_limit=int(data.get("reflection_limit", cls.reflection_limit)),
            prompt_max_chars=int(data.get("prompt_max_chars", cls.prompt_max_chars)),
        )


@dataclass(slots=True)
class BotSettings:
    database_path: str = ".iamai/yqy_bot.sqlite3"
    max_history_turns: int = 16
    long_context_turns: int = 12
    private_cooldown_seconds: int = 3
    group_cooldown_seconds: int = 12
    group_heat_window_seconds: int = 300
    quiet_threshold: int = 3
    active_threshold: int = 6
    hot_threshold: int = 12
    flood_threshold: int = 20
    background_batch_size: int = 6
    background_flush_seconds: float = 12.0
    group_trigger_keywords: list[str] = field(
        default_factory=lambda: ["YHarvest", "机器人"]
    )
    private_trigger_keywords: list[str] = field(default_factory=list)
    blocked_group_ids: list[str] = field(default_factory=list)
    blocked_user_ids: list[str] = field(default_factory=list)
    superusers: list[str] = field(default_factory=list)  # 特权用户，跳过所有限制
    allow_private_short_reply: bool = True
    allow_group_short_reply: bool = False
    default_group_mode: str = "quiet"
    napcat: NapCatSettings = field(default_factory=NapCatSettings)
    context: BotContextSettings = field(default_factory=BotContextSettings)
    search_mcp: SearchMCPSettings = field(default_factory=SearchMCPSettings)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "BotSettings":
        """从字典对象创建 BotSettings 实例。

        Args:
            payload: 配置字典，可选

        Returns:
            BotSettings 实例，缺失字段使用默认值填充
        """
        data = dict(payload or {})
        return cls(
            database_path=str(data.get("database_path", cls.database_path)),
            max_history_turns=int(data.get("max_history_turns", cls.max_history_turns)),
            long_context_turns=int(
                data.get("long_context_turns", cls.long_context_turns)
            ),
            private_cooldown_seconds=int(
                data.get("private_cooldown_seconds", cls.private_cooldown_seconds)
            ),
            group_cooldown_seconds=int(
                data.get("group_cooldown_seconds", cls.group_cooldown_seconds)
            ),
            group_heat_window_seconds=int(
                data.get("group_heat_window_seconds", cls.group_heat_window_seconds)
            ),
            quiet_threshold=int(data.get("quiet_threshold", cls.quiet_threshold)),
            active_threshold=int(data.get("active_threshold", cls.active_threshold)),
            hot_threshold=int(data.get("hot_threshold", cls.hot_threshold)),
            flood_threshold=int(data.get("flood_threshold", cls.flood_threshold)),
            background_batch_size=int(
                data.get("background_batch_size", cls.background_batch_size)
            ),
            background_flush_seconds=float(
                data.get("background_flush_seconds", cls.background_flush_seconds)
            ),
            group_trigger_keywords=[
                str(item)
                for item in data.get(
                    "group_trigger_keywords", DEFAULT_GROUP_TRIGGER_KEYWORDS
                )
            ],
            private_trigger_keywords=[
                str(item) for item in data.get("private_trigger_keywords", [])
            ],
            blocked_group_ids=[str(item) for item in data.get("blocked_group_ids", [])],
            blocked_user_ids=[str(item) for item in data.get("blocked_user_ids", [])],
            superusers=[str(item) for item in data.get("superusers", [])],
            allow_private_short_reply=bool(
                data.get("allow_private_short_reply", cls.allow_private_short_reply)
            ),
            allow_group_short_reply=bool(
                data.get("allow_group_short_reply", cls.allow_group_short_reply)
            ),
            default_group_mode=str(
                data.get("default_group_mode", cls.default_group_mode)
            ),
            napcat=NapCatSettings.from_mapping(data.get("napcat", {})),
            context=BotContextSettings.from_mapping(data.get("context", {})),
            search_mcp=SearchMCPSettings.from_mapping(
                data.get("search_mcp", data.get("tools", {}).get("search_mcp", {}))
            ),
        )


@dataclass(slots=True)
class PersonaSettings:
    identity: str = "你是一个 QQ 被动聊天机器人。"
    personality: list[str] = field(default_factory=list)
    scenario: str = ""
    speech_style: list[str] = field(default_factory=list)
    fact_boundary: list[str] = field(default_factory=list)
    memory_rules: list[str] = field(default_factory=list)
    reflection_rules: list[str] = field(default_factory=list)
    tone: list[str] = field(default_factory=list)  # 兼容旧字段
    reply_rules: list[str] = field(default_factory=list)  # 兼容旧字段
    examples: dict[str, Any] = field(default_factory=dict)  # 改为 dict 支持新结构
    output_schema: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "PersonaSettings":
        """从字典对象创建 PersonaSettings 实例。

        Args:
            payload: 配置字典，可选

        Returns:
            PersonaSettings 实例，缺失字段使用默认值填充
        """
        data = dict(payload or {})
        return cls(
            identity=str(data.get("identity", cls.identity)),
            personality=[str(item) for item in data.get("personality", [])],
            scenario=str(data.get("scenario", "")),
            speech_style=[str(item) for item in data.get("speech_style", [])],
            fact_boundary=[str(item) for item in data.get("fact_boundary", [])],
            memory_rules=[str(item) for item in data.get("memory_rules", [])],
            reflection_rules=[str(item) for item in data.get("reflection_rules", [])],
            tone=[str(item) for item in data.get("tone", DEFAULT_PERSONA_TONE)],
            reply_rules=[
                str(item) for item in data.get("reply_rules", DEFAULT_PERSONA_RULES)
            ],
            examples=dict(data.get("examples", {})),
            output_schema=[str(item) for item in data.get("output_schema", [])],
        )


@dataclass(slots=True)
class SafetyFallback:
    text: str = "行，我收敛点，不乱给你加剧情。"
    face_id: str = ""
    send_face: bool = False
    send_mface: bool = False
    mface: dict[str, Any] = field(default_factory=dict)
    send_image: bool = False
    image_url: str = ""
    at_user_id: str = ""

    def to_response(self) -> "GeneratedResponse":
        """将安全降级配置转换为回复对象。

        Returns:
            GeneratedResponse 实例，包含降级文本和可选的表情设置
        """
        return GeneratedResponse(
            text=self.text,
            send_face=self.send_face,
            face_id=self.face_id,
            send_mface=self.send_mface,
            mface=dict(self.mface),
            send_image=self.send_image,
            image_url=self.image_url,
            at_user_id=self.at_user_id,
        )


@dataclass(slots=True)
class SafetySettings:
    fact_boundary: list[str] = field(default_factory=list)
    memory_rules: list[str] = field(default_factory=list)
    reflection_rules: list[str] = field(default_factory=list)
    low_info_skip_keywords: list[str] = field(
        default_factory=lambda: [
            "哈哈",
            "哈哈哈",
            "在吗",
            "晚安",
            "早安",
            "收到",
            "OK",
            "ok",
            "好的",
        ]
    )
    low_info_max_chars: int = 8
    question_mark_tokens: list[str] = field(
        default_factory=lambda: ["?", "？", "吗", "呢"]
    )
    fake_fact_keywords: list[str] = field(default_factory=list)
    high_risk_triggers: list[str] = field(default_factory=list)
    toxic_patterns: list[str] = field(default_factory=list)
    toxic_fallback: SafetyFallback = field(default_factory=SafetyFallback)
    fallback_reply: SafetyFallback = field(default_factory=SafetyFallback)
    rewrite_prompt: str = (
        "下面这句回复包含没有依据的过去经历或具体事实，请删除所有无依据细节，只保留基于当前用户消息的轻微调侃。"
        "不要提过去，不要提具体时间、次数、地点、外貌、红包、聊天记录。最多一句话，返回 JSON："
        '{"text":"重写后的回复", "face_id":"", "send_meme":false}'
    )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "SafetySettings":
        """从字典对象创建 SafetySettings 实例。

        Args:
            payload: 配置字典，可选

        Returns:
            SafetySettings 实例，缺失字段使用默认值填充
        """
        data = dict(payload or {})
        toxic_fallback = data.get("toxic_fallback", {})
        fallback_reply = data.get("fallback_reply", {})
        return cls(
            fact_boundary=[str(item) for item in data.get("fact_boundary", [])],
            memory_rules=[str(item) for item in data.get("memory_rules", [])],
            reflection_rules=[str(item) for item in data.get("reflection_rules", [])],
            low_info_skip_keywords=[
                str(item)
                for item in data.get(
                    "low_info_skip_keywords", DEFAULT_LOW_INFO_SKIP_KEYWORDS
                )
            ],
            low_info_max_chars=int(
                data.get("low_info_max_chars", cls.low_info_max_chars)
            ),
            question_mark_tokens=[
                str(item)
                for item in data.get("question_mark_tokens", DEFAULT_QUESTION_TOKENS)
            ],
            fake_fact_keywords=[
                str(item) for item in data.get("fake_fact_keywords", [])
            ],
            high_risk_triggers=[
                str(item) for item in data.get("high_risk_triggers", [])
            ],
            toxic_patterns=[str(item) for item in data.get("toxic_patterns", [])],
            toxic_fallback=SafetyFallback(**_coerce_fallback(toxic_fallback)),
            fallback_reply=SafetyFallback(**_coerce_fallback(fallback_reply)),
            rewrite_prompt=str(data.get("rewrite_prompt", cls.rewrite_prompt)),
        )


@dataclass(slots=True)
class ParsedMessage:
    event_id: str
    adapter: str
    platform: str
    self_id: str
    user_id: str
    group_id: str
    message_id: str
    message_type: str
    sender_nickname: str
    sender_card: str
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    segments: list[dict[str, Any]] = field(default_factory=list)
    raw_segments: list[dict[str, Any]] = field(default_factory=list)
    is_at_bot: bool = False
    mention_user_ids: list[str] = field(default_factory=list)
    has_image: bool = False
    reply_message_id: str = ""
    sender_display_name: str = ""
    is_group: bool = False
    is_private: bool = False

    @property
    def chat_key(self) -> str:
        """获取聊天的唯一标识符。

        Returns:
            聊天键字符串，格式为 "{scope_type}:{scope_id}"
        """
        return self.session_id

    @property
    def session_id(self) -> str:
        """获取会话标识符。

        Returns:
            会话 ID 字符串，群聊为 "group:{group_id}"，私聊为 "private:{user_id}"
        """
        if self.is_group:
            return f"group:{self.group_id}"
        return f"private:{self.user_id}"

    @property
    def scope_type(self) -> str:
        """获取范围类型。

        Returns:
            范围类型字符串，群聊返回 "group"，私聊返回 "private"
        """
        return "group" if self.is_group else "private"

    @property
    def scope_id(self) -> str:
        """获取范围 ID。

        Returns:
            群聊返回群号，私聊返回用户 ID
        """
        return self.group_id if self.is_group else self.user_id

    @property
    def mentioned_bot(self) -> bool:
        """判断消息是否 @ 了机器人。

        Returns:
            如果消息包含 @ 机器人则返回 True
        """
        return self.is_at_bot

    @property
    def replied_to_bot(self) -> bool:
        """判断消息是否引用回复了机器人。

        Returns:
            如果消息包含引用回复则返回 True
        """
        return bool(self.reply_message_id)

    @property
    def is_short(self) -> bool:
        """判断消息是否为短消息（长度不超过 8 个字符）。

        Returns:
            如果消息文本长度 <= 8 则返回 True，否则返回 False
        """
        return len(self.text.strip()) <= 8


@dataclass(slots=True)
class ChatHistoryItem:
    id: int
    chat_key: str
    scope_type: str
    scope_id: str
    message_id: str
    role: str
    user_id: str
    content: str
    created_at: str
    source: str = "event"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GateDecision:
    allow: bool
    group_mode: str
    reason: str = ""
    should_dispatch_background: bool = True
    is_triggered: bool = False
    is_cooldown_blocked: bool = False


@dataclass(slots=True)
class IntentDecision:
    should_reply: bool
    reply_style: str = "normal"
    need_reason_model: bool = False
    need_emoji: bool = False
    confidence: float = 0.0
    reply_length: str = "normal"
    notes: str = ""
    # 搜索相关字段
    need_search: bool = False
    search_query: str = ""
    need_extract: bool = False
    extract_url: str = ""
    search_reason: str = ""


@dataclass(slots=True)
class SearchDecision:
    """搜索决策对象，包含是否需要搜索以及搜索参数。"""

    need_search: bool = False
    search_query: str = ""
    need_extract: bool = False
    extract_url: str = ""
    reason: str = ""


@dataclass(slots=True)
class SearchResult:
    """搜索结果对象，包含搜索返回的数据或错误信息。"""

    ok: bool = False
    type: str = ""  # "web_search" or "web_extract"
    query: str = ""
    provider: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    message: str = ""


@dataclass(slots=True)
class GeneratedResponse:
    text: str = ""
    send_face: bool = False
    face_id: str = ""
    send_mface: bool = False
    mface: dict[str, Any] = field(default_factory=dict)
    send_image: bool = False
    image_url: str = ""
    at_user_id: str = ""
    reply_to_message_id: str = ""

    def normalized(self) -> "GeneratedResponse":
        """规范化回复对象，清理空白字段并验证一致性。

        Returns:
            规范化后的 GeneratedResponse 实例，空白字段已移除，
            表情和图片字段仅在有效时保留
        """
        return GeneratedResponse(
            text=str(self.text or "").strip(),
            send_face=bool(self.send_face and self.face_id),
            face_id=str(self.face_id or "").strip(),
            send_mface=bool(self.send_mface and self.mface),
            mface=dict(self.mface or {}),
            send_image=bool(self.send_image and self.image_url),
            image_url=str(self.image_url or "").strip(),
            at_user_id=str(self.at_user_id or "").strip(),
            reply_to_message_id=str(self.reply_to_message_id or "").strip(),
        )


@dataclass(slots=True)
class ConversationContext:
    parsed: ParsedMessage
    recent_history: list[ChatHistoryItem]
    summary: str
    user_profile: dict[str, Any]
    group_profile: dict[str, Any]
    long_context: str
    persona: PersonaSettings
    safety: SafetySettings
    bot: BotSettings
    extra_notes: list[str] = field(default_factory=list)
    context_data: dict[str, Any] = field(default_factory=dict)
    prompt_md: str = ""
    user_profile_md: str = ""
    group_profile_md: str = ""
    relevant_memories: list[dict[str, Any]] = field(default_factory=list)
    reflections: list[dict[str, Any]] = field(default_factory=list)
    intent_decision: dict[str, Any] = field(default_factory=dict)
    group_heat_state: str = "quiet"
    context_stats: dict[str, Any] = field(default_factory=dict)

    def build_system_prompt(self) -> str:
        """构建系统提示词，包含人设、历史摘要和额外提示。

        Returns:
            系统提示词字符串，各部分用换行符分隔
        """
        sections = [
            self.persona.identity.strip(),
            " ".join(self.persona.tone).strip(),
            "；".join(self.persona.reply_rules).strip(),
        ]
        if self.summary.strip():
            sections.append(f"历史摘要：{self.summary.strip()}")
        if self.user_profile:
            sections.append(f"用户画像：{_format_mapping(self.user_profile)}")
        if self.group_profile:
            sections.append(f"群聊画像：{_format_mapping(self.group_profile)}")
        if self.long_context.strip():
            sections.append(f"长上下文：{self.long_context.strip()}")
        if self.extra_notes:
            sections.append("额外提示：" + "；".join(self.extra_notes))
        return "\n".join(section for section in sections if section)

    def to_context_data(self) -> dict[str, Any]:
        """返回结构化上下文数据，供 PromptBuilder 使用。"""
        if self.context_data:
            return dict(self.context_data)
        return {
            "current_message": {
                "text": self.parsed.text,
                "user_id": self.parsed.user_id,
                "group_id": self.parsed.group_id,
                "session_id": self.parsed.session_id,
                "is_group": self.parsed.is_group,
                "is_private": self.parsed.is_private,
                "is_at_bot": self.parsed.is_at_bot,
                "reply_message_id": self.parsed.reply_message_id,
                "sender_display_name": self.parsed.sender_display_name,
            },
            "recent_turns": [
                {
                    "role": item.role,
                    "content": item.content,
                    "user_id": item.user_id,
                    "sender_display_name": item.metadata.get("sender_display_name", ""),
                    "message_id": item.metadata.get("message_id", ""),
                }
                for item in self.recent_history
            ],
            "chat_summary": self.summary,
            "user_profile_md": self.user_profile_md,
            "group_profile_md": self.group_profile_md,
            "relevant_memories": list(self.relevant_memories),
            "reflections": list(self.reflections),
            "intent_decision": dict(self.intent_decision),
            "group_heat_state": self.group_heat_state,
            "context_stats": dict(self.context_stats),
        }

    def build_messages(self) -> list[dict[str, str]]:
        """构建完整的消息列表，包含系统提示和用户消息。

        Returns:
            消息列表，第一条为系统提示词，第二条为用户消息，
            包含当前消息、聊天信息和最近对话历史
        """
        transcript = []
        for item in self.recent_history:
            transcript.append(f"{item.role}: {item.content}")
        history_block = "\n".join(transcript).strip() or "（无）"
        return [
            {"role": "system", "content": self.build_system_prompt()},
            {
                "role": "user",
                "content": (
                    f"当前消息：{self.parsed.text}\n"
                    f"聊天类型：{self.parsed.scope_type}\n"
                    f"聊天键：{self.parsed.chat_key}\n"
                    f"是否群聊：{str(self.parsed.is_group).lower()}\n"
                    f"是否私聊：{str(self.parsed.is_private).lower()}\n"
                    f"是否被 @：{str(self.parsed.mentioned_bot).lower()}\n"
                    f"是否引用：{str(self.parsed.replied_to_bot).lower()}\n"
                    f"最近对话：\n{history_block}"
                ),
            },
        ]


def _coerce_fallback(payload: dict[str, Any] | Any) -> dict[str, Any]:
    """将降级配置转换为字典，验证并填充默认值。

    Args:
        payload: 原始降级配置数据，可以是任意类型

    Returns:
        规范化后的字典，包含 text、face_id 等字段
    """
    if not isinstance(payload, dict):
        return {}
    mface = payload.get("mface", {})
    return {
        "text": str(payload.get("text", "")),
        "face_id": str(payload.get("face_id", "")),
        "send_face": bool(payload.get("send_face", False)),
        "send_mface": bool(payload.get("send_mface", False)),
        "mface": dict(mface) if isinstance(mface, dict) else {},
        "send_image": bool(payload.get("send_image", False)),
        "image_url": str(payload.get("image_url", "")),
        "at_user_id": str(payload.get("at_user_id", "")),
    }


def _format_mapping(payload: dict[str, Any]) -> str:
    """格式化字典为可读的字符串表示。

    Args:
        payload: 待格式化的字典

    Returns:
        格式化后的字符串，格式为 "key1=value1; key2=value2"
    """
    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, (list, tuple, set)):
            rendered = ", ".join(str(item) for item in value)
        elif isinstance(value, dict):
            rendered = _format_mapping(value)
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return "; ".join(parts)
