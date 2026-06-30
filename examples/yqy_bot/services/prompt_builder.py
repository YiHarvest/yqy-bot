"""系统提示词构建服务。

从 chat.py 中提取，负责构建酒馆角色卡结构的系统提示词。
顺序：状态 → 关系 → 人格 → 记忆 → 反思 → 社交 → 约束 → 示例 → 输出格式
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .config_service import PROMPT_CONFIG

if TYPE_CHECKING:
    from .mood import MoodService
    from .relation import RelationshipService
    from .persona import PersonaService
    from .memory import MemoryService
    from .reflection import ReflectionService
    from .social_memory import SocialMemoryService


# 从配置中提取静态 prompt 字段
_IDENTITY: str = PROMPT_CONFIG.get("identity", "")
_PERSONALITY: list[str] = PROMPT_CONFIG.get("personality", [])
_SCENARIO: str = PROMPT_CONFIG.get("scenario", "")
_SPEECH_STYLE: list[str] = PROMPT_CONFIG.get("speech_style", [])
_FACT_BOUNDARY: list[str] = PROMPT_CONFIG.get("fact_boundary", [])
_MEMORY_RULES: list[str] = PROMPT_CONFIG.get("memory_rules", [])
_REFLECTION_RULES: list[str] = PROMPT_CONFIG.get("reflection_rules", [])
_EXAMPLES: dict[str, Any] = PROMPT_CONFIG.get("examples", {})
_OUTPUT_SCHEMA: list[str] = PROMPT_CONFIG.get("output_schema", [])


class PromptBuilder:
    """系统提示词构建器。"""

    def __init__(
        self,
        mood_service: "MoodService",
        relation_service: "RelationshipService",
        persona_service: "PersonaService",
        memory_service: "MemoryService",
        reflection_service: "ReflectionService",
        social_memory_service: "SocialMemoryService",
    ) -> None:
        self._mood = mood_service
        self._relation = relation_service
        self._persona = persona_service
        self._memory = memory_service
        self._reflection = reflection_service
        self._social = social_memory_service

    def build(self, user_id: str) -> str:
        """构建完整的系统提示词。

        Args:
            user_id: 用户 ID

        Returns:
            完整的系统提示词文本
        """
        memories = self._memory.get_memories(user_id)
        reflections = self._reflection.get_recent()
        social_desc = self._social.describe_user(user_id)

        parts: list[str] = []

        # 1. 当前时间
        self._append_time_section(parts)

        # 2. 星星当前状态
        self._append_persona_state(parts, user_id)

        # 3. 关系上下文
        self._append_relationship(parts, user_id)

        # 4. 人格设定（静态）
        self._append_identity(parts)

        # 5. 长期记忆
        self._append_memories(parts, memories)

        # 6. 反思观察
        self._append_reflections(parts, reflections)

        # 7. 社交记忆
        self._append_social(parts, social_desc)

        # 8. 事实边界
        self._append_fact_boundary(parts)

        # 9. 记忆使用规则
        self._append_memory_rules(parts)

        # 10. 反思使用规则
        self._append_reflection_rules(parts)

        # 11. 说话风格
        self._append_speech_style(parts)

        # 12. 示例对话
        self._append_examples(parts)

        # 13. 输出格式
        self._append_output_schema(parts)

        return "\n".join(parts)

    def _append_time_section(self, parts: list[str]) -> None:
        """添加当前时间部分。"""
        now = datetime.now()
        time_str = now.strftime("%Y年%m月%d日 %H:%M")
        weekday = "一二三四五六日"[now.weekday()]
        parts.append(
            f"【当前北京时间：{time_str} 周{weekday}】以上是真实时间，回答涉及时间时必须以这个时间为准。"
        )
        parts.append("")

    def _append_persona_state(self, parts: list[str], user_id: str) -> None:
        """添加人格状态部分。"""
        parts.append(self._mood.describe())
        parts.append("")
        rel = self._relation.get_or_create_user(user_id)
        mood_state = self._mood.get_state()
        parts.append(self._persona.describe(rel["identity"], mood_state))

    def _append_relationship(self, parts: list[str], user_id: str) -> None:
        """添加关系上下文部分。"""
        parts.append("")
        parts.append(self._relation.describe(user_id))

    def _append_identity(self, parts: list[str]) -> None:
        """添加人格设定部分。"""
        parts.append("")
        parts.append(_IDENTITY)
        parts.append("")
        parts.append("## 性格")
        for line in _PERSONALITY:
            parts.append(f"- {line}")
        parts.append("")
        parts.append(f"## 场景\n{_SCENARIO}")

    def _append_memories(self, parts: list[str], memories: list[str]) -> None:
        """添加长期记忆部分。"""
        if memories:
            parts.append("")
            parts.append("## 长期记忆（参考，非绝对事实）")
            for m in memories:
                parts.append(f"- {m}")

    def _append_reflections(self, parts: list[str], reflections: list[str]) -> None:
        """添加反思观察部分。"""
        if reflections:
            parts.append("")
            parts.append("## 星星的近期观察（主观判断，非事实）")
            for r in reflections:
                parts.append(f"- {r}")

    def _append_social(self, parts: list[str], social_desc: str) -> None:
        """添加社交记忆部分。"""
        if social_desc:
            parts.append("")
            parts.append("## 人物关系记忆")
            parts.append(social_desc)

    def _append_fact_boundary(self, parts: list[str]) -> None:
        """添加事实边界部分。"""
        parts.append("")
        parts.append("# 事实边界（最高优先级，必须遵守）")
        for line in _FACT_BOUNDARY:
            parts.append(line)

    def _append_memory_rules(self, parts: list[str]) -> None:
        """添加记忆使用规则部分。"""
        parts.append("")
        parts.append("## 记忆使用规则")
        for line in _MEMORY_RULES:
            parts.append(f"- {line}")

    def _append_reflection_rules(self, parts: list[str]) -> None:
        """添加反思使用规则部分。"""
        parts.append("")
        parts.append("## 反思使用规则")
        for line in _REFLECTION_RULES:
            parts.append(f"- {line}")

    def _append_speech_style(self, parts: list[str]) -> None:
        """添加说话风格部分。"""
        parts.append("")
        parts.append("## 说话风格（必须遵守）")
        for line in _SPEECH_STYLE:
            parts.append(f"- {line}")

    def _append_examples(self, parts: list[str]) -> None:
        """添加示例对话部分。"""
        good = _EXAMPLES.get("good", [])
        bad = _EXAMPLES.get("bad", [])
        if not good and not bad:
            return

        parts.append("")
        parts.append("## 示例对话")

        if good:
            parts.append("### 正确回复")
            for ex in good:
                user_text = ex.get("user", "")
                star_text = ex.get("star", "")
                parts.append(f"用户：{user_text}")
                parts.append(f"星星：{star_text}")
                parts.append("")

        if bad:
            parts.append("### 错误回复（禁止使用类似表达）")
            for b in bad:
                parts.append(f"- 禁止：{b}")

    def _append_output_schema(self, parts: list[str]) -> None:
        """添加输出格式部分。"""
        parts.append("")
        parts.append("## 输出格式")
        for line in _OUTPUT_SCHEMA:
            parts.append(line)

        # 时间强调（末尾最强约束）
        now = datetime.now()
        weekday = "一二三四五六日"[now.weekday()]
        parts.append("")
        parts.append(
            f"【再次强调：今天是 {now.strftime('%Y年%m月%d日')} 周{weekday}，回答涉及日期、时间时必须以这个为准。】"
        )


def build_user_message_with_time(user_message: str) -> str:
    """在用户消息前注入当前时间。

    Args:
        user_message: 原始用户消息

    Returns:
        带时间前缀的用户消息
    """
    now = datetime.now()
    weekday = "一二三四五六日"[now.weekday()]
    time_prefix = f"[当前真实时间：{now.strftime('%Y年%m月%d日 %H:%M')} 周{weekday}]\n"
    return time_prefix + user_message
