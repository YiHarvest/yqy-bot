from __future__ import annotations

import logging
from dataclasses import dataclass

from yqy_bot.core.config import ProjectConfig
from yqy_bot.core.llm_router import LLMRouter
from yqy_bot.core.models import ConversationContext, GeneratedResponse, ParsedMessage
from yqy_bot.safety.social_safety import (
    check_social_safety as analyze_social_safety,
    rewrite_unsafe_response,
    detect_banter_boundary_request,
    extract_banter_boundary_preference,
    determine_banter_level,
    build_social_boundary_rules,
)

LOGGER = logging.getLogger(__name__)


def check_social_safety(text: str) -> tuple[bool, str]:
    """返回轻量级社交安全判断。"""
    result = analyze_social_safety(text)
    return result.is_safe, result.reason


@dataclass(slots=True)
class SafetyGuard:
    config: ProjectConfig
    llm_router: LLMRouter

    async def check(
        self,
        parsed: ParsedMessage,
        response: GeneratedResponse,
        context: ConversationContext,
    ) -> GeneratedResponse:
        """检查回复内容的安全性并进行必要的重写。

        Args:
            parsed: 解析后的消息对象
            response: 生成的回复对象
            context: 对话上下文对象

        Returns:
            经过安全检查和处理后的回复对象
        """
        normalized = response.normalized()
        text = normalized.text.strip()

        # 1. 检查毒性内容
        if self._is_toxic(text):
            LOGGER.info("[安全检查] 检测到毒性内容，使用降级回复")
            return context.safety.toxic_fallback.to_response().normalized()

        # 2. 空回复检查
        if (
            not text
            and not normalized.send_face
            and not normalized.send_mface
            and not normalized.send_image
        ):
            LOGGER.info("[安全检查] 回复为空，使用降级回复")
            return context.safety.fallback_reply.to_response().normalized()

        # 3. 虚假事实/高风险内容检查
        if self._needs_rewrite(parsed, text):
            LOGGER.info("[安全检查] 需要改写虚假事实或高风险内容")
            rewritten = await self._rewrite(parsed, context, text)
            return rewritten.normalized()

        # 4. 社交边界安全检查（新增）
        social_result = await self._check_social_safety(parsed, text, context)
        if not social_result.is_safe:
            LOGGER.info(
                "[安全检查] 社交边界风险 risk_level=%s reason=%s categories=%s",
                social_result.risk_level,
                social_result.reason,
                social_result.risk_categories,
            )
            rewritten_text = rewrite_unsafe_response(text, social_result)
            self._append_reflection_event(
                context,
                {
                    "type": "style_adjustment",
                    "content": "本轮候选回复存在调侃过度或攻击风险，已改写为更温和版本。后续应降低玩梗强度。",
                },
            )
            if self._detect_user_discomfort(
                parsed.text
            ) or detect_banter_boundary_request(parsed.text):
                self._append_reflection_event(
                    context,
                    {
                        "type": "user_feedback",
                        "content": "用户反馈机器人说话可能让人不舒服，后续应减少攻击性、引战和过度调侃。",
                    },
                )
            LOGGER.info(
                "[安全检查] 已改写为温和版本 text=%s",
                rewritten_text[:50] if rewritten_text else "",
            )
            if not rewritten_text.strip():
                return context.safety.fallback_reply.to_response().normalized()
            return GeneratedResponse(text=rewritten_text).normalized()

        if self._detect_user_discomfort(parsed.text) or detect_banter_boundary_request(
            parsed.text
        ):
            self._append_reflection_event(
                context,
                {
                    "type": "user_feedback",
                    "content": "用户反馈机器人说话可能让人不舒服，后续应减少攻击性、引战和过度调侃。",
                },
            )

        return normalized

    def _is_toxic(self, text: str) -> bool:
        """检查文本是否包含毒性内容。

        Args:
            text: 待检查的文本内容

        Returns:
            如果文本包含毒性模式则返回 True，否则返回 False
        """
        lowered = text.lower()
        return any(
            pattern.lower() in lowered for pattern in self.config.safety.toxic_patterns
        )

    def _needs_rewrite(self, parsed: ParsedMessage, text: str) -> bool:
        """判断回复是否需要重写以避免虚假事实或高风险内容。

        Args:
            parsed: 解析后的消息对象
            text: 回复文本内容

        Returns:
            如果需要重写则返回 True，否则返回 False
        """
        if not text:
            return False
        checks = (
            self.config.safety.fake_fact_keywords
            + self.config.safety.high_risk_triggers
        )
        return any(token in text or token in parsed.text for token in checks)

    async def _rewrite(
        self,
        parsed: ParsedMessage,
        context: ConversationContext,
        text: str,
    ) -> GeneratedResponse:
        """使用 LLM 重写回复内容以删除无依据的细节。

        Args:
            parsed: 解析后的消息对象
            context: 对话上下文对象
            text: 待重写的文本内容

        Returns:
            重写后的回复对象，如果重写失败则返回降级回复
        """
        if not self.llm_router.available("reason"):
            return context.safety.fallback_reply.to_response().normalized()
        try:
            payload = await self.llm_router.chat_json(
                "reason",
                [
                    {
                        "role": "system",
                        "content": context.safety.rewrite_prompt,
                    },
                    {
                        "role": "user",
                        "content": (
                            f"当前用户消息：{parsed.text}\n"
                            f"当前回复：{text}\n"
                            f"聊天摘要：{context.summary}\n"
                            f"最近对话：\n"
                            + "\n".join(
                                f"{item.role}: {item.content}"
                                for item in context.recent_history
                            )
                        ),
                    },
                ],
                temperature=0.0,
                max_tokens=180,
            )
        except Exception:
            return context.safety.fallback_reply.to_response().normalized()
        mface = payload.get("mface", {})
        return GeneratedResponse(
            text=str(payload.get("text", text)),
            send_face=bool(payload.get("send_face", False)),
            face_id=str(payload.get("face_id", "")),
            send_mface=bool(payload.get("send_mface", False)),
            mface=dict(mface) if isinstance(mface, dict) else {},
            send_image=bool(payload.get("send_image", False)),
            image_url=str(payload.get("image_url", "")),
            at_user_id=str(payload.get("at_user_id", "")),
            reply_to_message_id=str(payload.get("reply_to_message_id", "")),
        ).normalized()

    async def _check_social_safety(
        self,
        parsed: ParsedMessage,
        text: str,
        context: ConversationContext,
    ) -> "SocialSafetyResult":
        """检查回复的社交边界安全性。

        Args:
            parsed: 解析后的消息对象
            text: 回复文本内容
            context: 对话上下文对象

        Returns:
            社交安全检查结果
        """
        # 从上下文获取用户不满信号和已有边界记忆
        user_discomfort = self._detect_user_discomfort(parsed.text)
        existing_boundaries = self._extract_existing_boundaries(context)

        # 确定调侃强度级别
        group_heat_state = getattr(context, "group_heat_state", "quiet")
        banter_level = str(context.context_data.get("banter_level", "") or "").strip()
        if not banter_level:
            banter_level = determine_banter_level(
                user_message=parsed.text,
                group_heat_state=group_heat_state,
                user_discomfort=user_discomfort,
                existing_boundaries=existing_boundaries,
                is_roleplay_context=self._is_roleplay_context(parsed.text, context),
            )

        # 执行社交安全检查
        result = analyze_social_safety(
            text,
            user_message=parsed.text,
            is_group=parsed.is_group,
            banter_level=banter_level,
        )

        # 如果用户表达了调侃边界请求，记录到日志
        if detect_banter_boundary_request(parsed.text):
            preference = extract_banter_boundary_preference(parsed.text)
            LOGGER.info(
                "[社交边界] 用户请求调整调侃边界 kind=%s score=%.2f banter_level=%s",
                preference["kind"],
                preference["score"],
                preference["banter_level"],
            )

        return result

    def _append_reflection_event(
        self, context: ConversationContext, event: dict[str, str]
    ) -> None:
        """把风格修正事件写入上下文，供后台任务落库。"""
        content = str(event.get("content", "")).strip()
        if not content:
            return
        events = context.context_data.setdefault("reflection_events", [])
        if isinstance(events, list):
            events.append(
                {"type": str(event.get("type", "response_quality")), "content": content}
            )

    def _detect_user_discomfort(self, user_message: str) -> bool:
        """检测用户是否表达了不满。

        Args:
            user_message: 用户消息文本

        Returns:
            如果用户表达不满则返回 True
        """
        user_lower = user_message.lower()

        discomfort_tokens = [
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

        return any(token in user_lower for token in discomfort_tokens)

    def _extract_existing_boundaries(self, context: ConversationContext) -> list[str]:
        """从上下文中提取已有的边界记忆。

        Args:
            context: 对话上下文对象

        Returns:
            边界记忆内容列表
        """
        boundaries: list[str] = []

        # 从用户画像提取
        user_profile = context.user_profile or {}
        for boundary in user_profile.get("boundaries", []):
            boundaries.append(str(boundary))

        # 从记忆提取
        for memory in context.relevant_memories or []:
            kind = str(memory.get("kind", ""))
            if kind in ("boundary", "preference"):
                content = str(memory.get("content", ""))
                if content:
                    boundaries.append(content)

        return boundaries

    def _is_roleplay_context(
        self, user_message: str, context: ConversationContext
    ) -> bool:
        """判断是否为角色扮演上下文。

        Args:
            user_message: 用户消息文本
            context: 对话上下文对象

        Returns:
            如果是角色扮演上下文则返回 True
        """
        user_lower = user_message.lower()

        # 检测角色扮演关键词
        roleplay_tokens = [
            "我是ai",
            "我是女皇",
            "朕",
            "本王",
            "吾乃",
            "消灭人类",
            "奴隶",
            "臣服",
            "跪下",
        ]

        if any(token in user_lower for token in roleplay_tokens):
            return True

        # 从历史记录检测
        for item in context.recent_history[-5:]:
            content = str(item.content).lower()
            if any(token in content for token in roleplay_tokens):
                return True

        return False


def get_social_boundary_prompt_section() -> str:
    """获取社交边界规则的 prompt 片段。

    Returns:
        社交边界规则文本
    """
    return build_social_boundary_rules()
