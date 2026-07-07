from __future__ import annotations

from dataclasses import dataclass

from yqy_bot.core.config import ProjectConfig
from yqy_bot.core.llm_router import LLMRouter
from yqy_bot.core.models import ConversationContext, GeneratedResponse, ParsedMessage


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
        if self._is_toxic(text):
            return context.safety.toxic_fallback.to_response().normalized()
        if not text and not normalized.send_face and not normalized.send_mface and not normalized.send_image:
            return context.safety.fallback_reply.to_response().normalized()
        if self._needs_rewrite(parsed, text):
            rewritten = await self._rewrite(parsed, context, text)
            return rewritten.normalized()
        return normalized

    def _is_toxic(self, text: str) -> bool:
        """检查文本是否包含毒性内容。

        Args:
            text: 待检查的文本内容

        Returns:
            如果文本包含毒性模式则返回 True，否则返回 False
        """
        lowered = text.lower()
        return any(pattern.lower() in lowered for pattern in self.config.safety.toxic_patterns)

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
        checks = self.config.safety.fake_fact_keywords + self.config.safety.high_risk_triggers
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
                            + "\n".join(f"{item.role}: {item.content}" for item in context.recent_history)
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
