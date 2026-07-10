from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from yqy_bot.core.models import ConversationContext
from yqy_bot.safety.social_safety import build_social_boundary_rules


@dataclass(slots=True)
class PromptBuilder:
    """提示词构建器，将对话上下文转换为 LLM 提示词。"""

    def build(self, context: ConversationContext) -> str:
        """构建完整的 LLM 提示词。

        将结构化上下文拼接为 Markdown 格式的提示词，
        包含人设、风格、规则、画像、历史、当前消息等。

        Args:
            context: 对话上下文对象

        Returns:
            提示词字符串，长度受 prompt_max_chars 配置限制
        """
        data = context.to_context_data()
        sections: list[str] = []
        # 新字段优先
        sections.append(context.persona.identity.strip())
        # 当前北京时间（关键：让 LLM 知道真实时间）
        now = datetime.now()
        current_time = now.strftime("%Y-%m-%d %H:%M:%S")
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][
            now.weekday()
        ]
        sections.append(f"【当前北京时间】{current_time} ({weekday})")
        sections.append(
            "⚠️ 时间优先级：【当前北京时间】是唯一正确的时间参考，历史对话中的时间信息可能已过时，请忽略历史中的错误时间。"
        )
        if context.persona.personality:
            sections.append("性格：" + " / ".join(context.persona.personality))
        if context.persona.scenario:
            sections.append("场景：" + context.persona.scenario)
        if context.persona.speech_style:
            sections.append("说话风格：" + "；".join(context.persona.speech_style))
        if context.persona.fact_boundary:
            sections.append("事实边界：" + "；".join(context.persona.fact_boundary))
        if context.persona.memory_rules:
            sections.append("记忆规则：" + "；".join(context.persona.memory_rules))
        if context.persona.reflection_rules:
            sections.append("反思规则：" + "；".join(context.persona.reflection_rules))
        # 兼容旧字段
        if context.persona.tone:
            sections.append("风格：" + " / ".join(context.persona.tone))
        if context.persona.reply_rules:
            sections.append("规则：" + "；".join(context.persona.reply_rules))
        if context.safety.fact_boundary:
            sections.append("安全边界：" + "；".join(context.safety.fact_boundary))
        # 社交边界规则（新增）
        sections.append("【社交边界】\n" + build_social_boundary_rules())
        banter_level = str(data.get("banter_level", "")).strip()
        if banter_level:
            sections.append(_render_banter_level_section(banter_level))
        # 示例（新结构）
        examples = context.persona.examples
        if isinstance(examples, dict):
            good_examples = examples.get("good", [])
            if good_examples:
                example_lines = []
                for ex in good_examples[:3]:
                    if isinstance(ex, dict) and "user" in ex and "star" in ex:
                        example_lines.append(f"用户：{ex['user']} → 你：{ex['star']}")
                if example_lines:
                    sections.append("回复示例：" + " | ".join(example_lines))
            bad_examples = examples.get("bad", [])
            if bad_examples:
                sections.append(
                    "禁止：" + "；".join(str(ex) for ex in bad_examples[:3])
                )
        # 输出 schema
        if context.persona.output_schema:
            sections.append(
                "输出格式：" + context.persona.output_schema[0]
                if context.persona.output_schema
                else ""
            )

        runtime_lines = []
        if data.get("group_heat_state"):
            runtime_lines.append(f"场景热度：{data['group_heat_state']}")
        runtime_state = data.get("runtime_state", {})
        if isinstance(runtime_state, dict) and runtime_state:
            runtime_lines.append(
                "运行状态：" + json.dumps(runtime_state, ensure_ascii=False)
            )
        reference_message = data.get("reference_message")
        if isinstance(reference_message, dict) and reference_message:
            runtime_lines.append(
                "引用消息：" + _render_reference_message(reference_message)
            )
        intent_decision = data.get("intent_decision", {})
        if isinstance(intent_decision, dict) and intent_decision:
            runtime_lines.append(
                "意图决策：" + json.dumps(intent_decision, ensure_ascii=False)
            )
        if data.get("memory_conflict_policy"):
            runtime_lines.append(f"记忆冲突策略：{data['memory_conflict_policy']}")
        if data.get("summary_policy"):
            runtime_lines.append(f"摘要策略：{data['summary_policy']}")
        if context.extra_notes:
            runtime_lines.append(
                "额外提示：" + "；".join(note for note in context.extra_notes if note)
            )
        runtime_lines.append(
            "冲突优先级：当前消息优先；不要让旧记忆或旧画像覆盖当前明确表达。"
        )
        if runtime_lines:
            sections.append("\n".join(runtime_lines))

        user_profile_md = str(data.get("user_profile_md", "")).strip()
        if user_profile_md:
            sections.append("用户画像：\n" + user_profile_md)
        group_profile_md = str(data.get("group_profile_md", "")).strip()
        if group_profile_md and bool(
            data.get("current_message", {}).get("is_group", False)
        ):
            sections.append("群画像：\n" + group_profile_md)
        chat_summary = str(data.get("chat_summary", "")).strip()
        if chat_summary:
            sections.append("会话摘要：\n" + chat_summary)
        memories = _render_items("相关记忆", data.get("relevant_memories", []), limit=5)
        if memories:
            sections.append(memories)
        recent_turns = _render_recent_turns(data.get("recent_turns", []))
        if recent_turns:
            sections.append("最近对话：\n" + recent_turns)
        # 搜索结果（新增）
        search_result = data.get("search_result")
        if search_result:
            search_section = _render_search_result(search_result)
            if search_section:
                sections.append(search_section)
        current = data.get("current_message", {})
        sections.append("当前消息：\n" + _render_current_message(current))
        sections.append(
            "输出要求：只输出 JSON。字段仅允许 text, send_face, face_id, send_mface, mface, send_image, image_url, at_user_id, reply_to_message_id。"
        )
        prompt = _join_with_cap(sections, context.bot.context.prompt_max_chars)
        context.prompt_md = prompt
        return prompt


def _render_current_message(current: Any) -> str:
    """渲染当前消息块为文本格式。

    Args:
        current: 当前消息字典

    Returns:
        格式化的当前消息文本块
    """
    if not isinstance(current, dict):
        return ""
    lines = [
        f"session_id: {current.get('session_id', '')}",
        f"is_group: {str(current.get('is_group', False)).lower()}",
        f"is_private: {str(current.get('is_private', False)).lower()}",
        f"is_at_bot: {str(current.get('is_at_bot', False)).lower()}",
        f"reply_message_id: {current.get('reply_message_id', '')}",
        f"sender_display_name: {current.get('sender_display_name', '')}",
        f"text: {current.get('text', '')}",
    ]
    return "\n".join(lines)


def _render_items(title: str, items: Any, *, limit: int) -> str:
    """渲染列表项为文本格式。

    Args:
        title: 标题文本
        items: 列表项数据
        limit: 最大项数

    Returns:
        格式化的列表文本块，如果列表为空则返回空字符串
    """
    if not isinstance(items, list) or not items:
        return ""
    rendered: list[str] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            rendered.append(
                "- "
                + ", ".join(f"{k}={v}" for k, v in item.items() if v not in (None, ""))
            )
        else:
            rendered.append(f"- {item}")
    return f"{title}:\n" + "\n".join(rendered)


def _render_recent_turns(turns: Any) -> str:
    """渲染最近对话轮次为文本格式。

    Args:
        turns: 对话轮次列表

    Returns:
        格式化的对话历史文本块
    """
    if not isinstance(turns, list) or not turns:
        return ""
    lines: list[str] = []
    for item in turns:
        if not isinstance(item, dict):
            continue
        display_name = str(
            item.get("sender_display_name") or item.get("user_id") or ""
        ).strip()
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if display_name:
            lines.append(f"[{display_name}] {content}")
        elif role:
            lines.append(f"[{role}] {content}")
        else:
            lines.append(content)
    return "\n".join(line for line in lines if line)


def _render_reference_message(message: dict[str, Any]) -> str:
    """渲染引用消息为文本格式。

    Args:
        message: 引用消息字典

    Returns:
        格式化的引用消息文本
    """
    sender = str(
        message.get("metadata", {}).get("sender_display_name", "") or ""
    ).strip()
    text = str(message.get("content", "")).strip()
    message_id = str(message.get("message_id", "")).strip()
    parts = []
    if sender:
        parts.append(f"[{sender}]")
    if message_id:
        parts.append(f"id={message_id}")
    if text:
        parts.append(text)
    return " ".join(parts)


def _render_banter_level_section(banter_level: str) -> str:
    """渲染调侃强度提示。"""
    hints = {
        "none": "严格收敛，不玩梗，不阴阳怪气，不挑衅。",
        "light": "只允许轻微接梗，避免攻击、站队和过度调侃。",
        "normal": "保持自然轻松，但不要升级成攻击或拱火。",
        "high": "可以稍微活跃，但仍要守住边界，别引战。",
    }
    hint = hints.get(banter_level, "")
    return f"当前调侃强度：{banter_level}" + (f"。{hint}" if hint else "")


def _render_search_result(result: dict[str, Any]) -> str:
    """渲染搜索结果为提示词段落。

    Args:
        result: 搜索结果字典

    Returns:
        格式化的搜索结果文本，如果搜索失败则返回空字符串
    """
    if not result.get("ok", False):
        # 搜索失败，返回提示信息
        message = result.get("message", "")
        if message:
            return f"【搜索结果】\n{message}\n回答时不要说自己不能联网，直接说明没有搜到可靠结果或当前信息不足。"
        return ""

    result_type = result.get("type", "")
    query = result.get("query", "")
    provider = result.get("provider", "")
    results = result.get("results", [])

    if result_type == "web_extract":
        # URL 抽取结果
        content = ""
        for item in results:
            content = str(item.get("content", ""))
            break
        # 限制抽取内容长度
        if len(content) > 4000:
            content = content[:4000] + "..."
        return f"【网页正文抽取】\nURL: {query}\n正文摘录:\n{content}"

    # web_search 结果
    lines = [f"【联网搜索结果】\nquery: {query}\nprovider: {provider}\n"]
    for i, item in enumerate(results[:5], 1):
        title = str(item.get("title", "") or item.get("name", ""))
        href = str(item.get("href", "") or item.get("url", ""))
        abstract = str(
            item.get("abstract", "")
            or item.get("snippet", "")
            or item.get("content", "")
        )
        # 限制摘要长度
        if len(abstract) > 500:
            abstract = abstract[:500] + "..."
        lines.append(f"{i}. {title}")
        if href:
            lines.append(f"   URL: {href}")
        if abstract:
            lines.append(f"   摘要: {abstract}")
        lines.append("")

    search_text = "\n".join(lines)
    # 搜索规则提示
    search_text += (
        "\n搜索结果使用规则：\n"
        "1. 必须优先依据搜索结果回答。\n"
        "2. 涉及时效性问题时，不能只依赖模型旧知识。\n"
        "3. 搜索结果不足、失败、冲突时，必须说明不确定。\n"
        "4. 不要编造不存在的来源。\n"
        "5. 回答里尽量带上来源标题或 URL。\n"
        "6. 群聊场景保持简洁，除非用户明确要求详细解释。"
    )
    return search_text


def _join_with_cap(sections: list[str], max_chars: int) -> str:
    """合并提示词段落并限制总长度。

    优先保留尾部段落（当前消息和输出要求），
    头部段落按可用空间截断。

    Args:
        sections: 段落列表
        max_chars: 最大字符数

    Returns:
        合并后的提示词字符串
    """
    parts = [part.strip() for part in sections if str(part).strip()]
    if max_chars <= 0:
        return "\n\n".join(parts)
    if not parts:
        return ""
    tail = parts[-2:] if len(parts) >= 2 else parts
    head = parts[:-2] if len(parts) >= 2 else []
    tail_text = "\n\n".join(tail)
    head_text = "\n\n".join(head)
    if (
        len(head_text) + len(tail_text) + (2 if head_text and tail_text else 0)
        <= max_chars
    ):
        return "\n\n".join(parts)
    reserve = len(tail_text) + (2 if head_text and tail_text else 0)
    available = max(0, max_chars - reserve)
    if not head_text:
        return tail_text[:max_chars]
    if available <= 0:
        return tail_text[:max_chars]
    trimmed_head = head_text[:available].rstrip()
    if trimmed_head:
        return f"{trimmed_head}\n\n{tail_text}"
    return tail_text[:max_chars]
