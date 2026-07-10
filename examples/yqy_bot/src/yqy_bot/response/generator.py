from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from yqy_bot.core.config import ProjectConfig
from yqy_bot.core.llm_router import LLMRouter
from yqy_bot.core.models import ConversationContext, GeneratedResponse, IntentDecision
from yqy_bot.context.prompt import PromptBuilder

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ResponseGenerator:
    config: ProjectConfig
    llm_router: LLMRouter
    prompt_builder: PromptBuilder = field(default_factory=PromptBuilder)

    async def generate(
        self, context: ConversationContext, intent: IntentDecision
    ) -> GeneratedResponse:
        """生成回复内容，根据意图决定使用推理模型或聊天模型。

        Args:
            context: 对话上下文对象，包含人设、历史、画像等信息
            intent: 意图决策对象，决定回复风格和使用的模型

        Returns:
            生成的回复对象，包含文本、表情、图片等字段，
            如果生成失败或内容无效则返回降级回复
        """
        role = "reason" if intent.need_reason_model else "chat"
        available = self.llm_router.available(role)
        LOGGER.info(
            "[回复生成] chat_key=%s role=%s available=%s reply_style=%s reply_length=%s",
            context.parsed.chat_key,
            role,
            available,
            intent.reply_style,
            intent.reply_length,
        )
        if not available:
            fallback = self._fallback(context, intent)
            LOGGER.info(
                "[回复生成] chat_key=%s 方式=fallback text=%s",
                context.parsed.chat_key,
                fallback.text[:50] if fallback.text else "",
            )
            return fallback
        payload = await self._generate_json(role, context, intent)
        response = self._parse_response(payload)
        if (
            not response.text
            and not response.send_face
            and not response.send_mface
            and not response.send_image
        ):
            fallback = self._fallback(context, intent)
            LOGGER.info(
                "[回复生成] chat_key=%s 方式=fallback(empty_result) text=%s",
                context.parsed.chat_key,
                fallback.text[:50] if fallback.text else "",
            )
            return fallback
        LOGGER.info(
            "[回复生成] chat_key=%s 方式=llm text=%s send_face=%s send_mface=%s send_image=%s",
            context.parsed.chat_key,
            response.text[:50] if response.text else "",
            response.send_face,
            response.send_mface,
            response.send_image,
        )
        return response

    async def _generate_json(
        self,
        role: str,
        context: ConversationContext,
        intent: IntentDecision,
    ) -> dict[str, object]:
        """使用 LLM 生成 JSON 格式的回复。

        Args:
            role: 模型角色名称，"chat" 或 "reason"
            context: 对话上下文对象
            intent: 意图决策对象

        Returns:
            LLM 返回的 JSON 对象，包含 text、send_face、face_id 等字段
        """
        messages = [
            {
                "role": "system",
                "content": (
                    f"{self.prompt_builder.build(context)}\n"
                    "你只输出 JSON，不要 Markdown，不要解释。"
                    "JSON 必须只包含这些字段：text, send_face, face_id, send_mface, mface, send_image, image_url, at_user_id, reply_to_message_id。"
                    "不要在文本里拼 CQ 码。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "current_message": context.parsed.text,
                        "chat_key": context.parsed.chat_key,
                        "scope_type": context.parsed.scope_type,
                        "scope_id": context.parsed.scope_id,
                        "is_private": context.parsed.is_private,
                        "is_group": context.parsed.is_group,
                        "reply_to_message_id": context.parsed.reply_message_id,
                        "intent": {
                            "should_reply": intent.should_reply,
                            "reply_style": intent.reply_style,
                            "need_reason_model": intent.need_reason_model,
                            "need_emoji": intent.need_emoji,
                            "confidence": intent.confidence,
                        },
                        "history": [
                            {"role": item.role, "content": item.content}
                            for item in context.recent_history
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        return await self.llm_router.chat_json(
            role, messages, temperature=0.4, max_tokens=512
        )

    def _parse_response(self, payload: dict[str, object]) -> GeneratedResponse:
        """解析 LLM 返回的 JSON 载荷为回复对象。

        Args:
            payload: LLM 返回的 JSON 对象

        Returns:
            规范化后的回复对象
        """
        mface = payload.get("mface", {})
        response = GeneratedResponse(
            text=str(payload.get("text", "")),
            send_face=bool(payload.get("send_face", False)),
            face_id=str(payload.get("face_id", "")),
            send_mface=bool(payload.get("send_mface", False)),
            mface=dict(mface) if isinstance(mface, dict) else {},
            send_image=bool(payload.get("send_image", False)),
            image_url=str(payload.get("image_url", "")),
            at_user_id=str(payload.get("at_user_id", "")),
            reply_to_message_id=str(payload.get("reply_to_message_id", "")),
        )
        return response.normalized()

    def _fallback(
        self, context: ConversationContext, intent: IntentDecision
    ) -> GeneratedResponse:
        """生成降级回复（LLM 不可用或生成失败时使用）。

        Args:
            context: 对话上下文对象
            intent: 意图决策对象

        Returns:
            降级回复对象，优先使用配置的降级文本，否则使用默认回复
        """
        if context.safety.fallback_reply.text.strip():
            return context.safety.fallback_reply.to_response().normalized()
        text = "嗯，我看到了。"
        if intent.reply_style == "gentle":
            text = "嗯，我在。"
        elif intent.reply_style == "short":
            text = "收到。"
        return GeneratedResponse(text=text).normalized()
