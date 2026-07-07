from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from yqy_bot.core.config import ProjectConfig
from yqy_bot.core.llm_router import LLMRouter
from yqy_bot.core.models import GateDecision, IntentDecision, ParsedMessage

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class IntentRouter:
    config: ProjectConfig
    llm_router: LLMRouter

    async def decide(self, parsed: ParsedMessage, gate: GateDecision) -> IntentDecision:
        """判断消息的意图并决定回复策略。

        Args:
            parsed: 解析后的消息对象
            gate: 门控决策对象

        Returns:
            意图决策对象，包含是否回复、回复风格、是否需要推理模型等信息
        """
        fallback = self._heuristic_decision(parsed, gate)
        # superusers 强制回复
        if self._is_superuser(parsed):
            LOGGER.info(
                "[意图识别] chat_key=%s user_id=%s 方式=superuser should_reply=True reply_style=%s",
                parsed.chat_key,
                parsed.user_id,
                fallback.reply_style,
            )
            return IntentDecision(
                should_reply=True,
                reply_style=fallback.reply_style,
                need_reason_model=fallback.need_reason_model,
                need_emoji=fallback.need_emoji,
                confidence=1.0,
                reply_length=fallback.reply_length,
                notes="superuser",
            )
        # 对于简单消息，跳过 LLM 调用，直接使用启发式规则
        if not gate.allow or not self.llm_router.available("intent") or self._is_simple_message(parsed, gate):
            LOGGER.info(
                "[意图识别] chat_key=%s user_id=%s 方式=heuristic skip_llm=%s gate_allow=%s should_reply=%s reply_style=%s",
                parsed.chat_key,
                parsed.user_id,
                self._is_simple_message(parsed, gate),
                gate.allow,
                fallback.should_reply,
                fallback.reply_style,
            )
            return fallback
        prompt = self._build_prompt(parsed, gate, fallback)
        try:
            payload = await self.llm_router.chat_json(
                "intent",
                prompt,
                temperature=0.0,
                max_tokens=256,
            )
        except Exception:
            LOGGER.info(
                "[意图识别] chat_key=%s user_id=%s 方式=heuristic(fallback) llm_error=True should_reply=%s reply_style=%s",
                parsed.chat_key,
                parsed.user_id,
                fallback.should_reply,
                fallback.reply_style,
            )
            return fallback
        result = self._merge(fallback, payload)
        LOGGER.info(
            "[意图识别] chat_key=%s user_id=%s 方式=llm should_reply=%s reply_style=%s need_reason=%s need_emoji=%s confidence=%.2f",
            parsed.chat_key,
            parsed.user_id,
            result.should_reply,
            result.reply_style,
            result.need_reason_model,
            result.need_emoji,
            result.confidence,
        )
        return result

    def _is_simple_message(self, parsed: ParsedMessage, gate: GateDecision) -> bool:
        """判断消息是否足够简单，可以跳过 LLM 意图识别。

        简单消息包括：
        - 低信息量消息（短消息、低信息关键词）
        - 被 @ 或回复机器人（明确触发）
        - 包含问号的消息（明确问题）
        - 私聊的非推理类消息

        Args:
            parsed: 解析后的消息对象
            gate: 门控决策对象

        Returns:
            如果是简单消息应跳过 LLM 则返回 True
        """
        text = parsed.text.strip()
        safety = self.config.safety
        # 低信息量消息
        low_info = len(text) <= safety.low_info_max_chars or any(keyword in text for keyword in safety.low_info_skip_keywords)
        if low_info:
            return True
        # 被 @ 或回复机器人
        if gate.is_triggered:
            return True
        # 包含问号
        if any(token in text for token in safety.question_mark_tokens):
            return True
        # 需要推理模型的消息不跳过
        if any(token in text for token in ("为什么", "怎么", "分析", "解释", "判断", "比较")):
            return False
        # 情感类消息不跳过
        if any(token in text for token in ("难受", "压力", "emo", "烦", "累", "委屈", "想哭")):
            return False
        # 私聊普通消息跳过 LLM
        if parsed.is_private:
            return True
        # 群聊 quiet 模式下的普通消息跳过
        if parsed.is_group and gate.group_mode == "quiet":
            return True
        return False

    def _is_superuser(self, parsed: ParsedMessage) -> bool:
        """判断用户是否为特权用户（superuser）。

        Args:
            parsed: 解析后的消息对象

        Returns:
            如果是 superuser 则返回 True
        """
        return parsed.user_id in set(self.config.bot.superusers)

    def _heuristic_decision(self, parsed: ParsedMessage, gate: GateDecision) -> IntentDecision:
        """使用启发式规则判断意图（LLM 不可用时的降级方案）。

        注意：门控已在 pipeline._reply_gate 中完成精细判断，
        此方法主要根据消息特征决定回复风格。

        Args:
            parsed: 解析后的消息对象
            gate: 门控决策对象

        Returns:
            基于规则的意图决策对象，考虑消息长度、情感、问题等因素
        """
        text = parsed.text.strip()
        safety = self.config.safety
        low_info = (
            len(text) <= safety.low_info_max_chars
            or any(keyword in text for keyword in safety.low_info_skip_keywords)
        )
        emotional = any(token in text for token in ("难受", "压力", "emo", "烦", "累", "委屈", "想哭"))
        reasoning = any(token in text for token in ("为什么", "怎么", "分析", "解释", "判断", "比较"))
        # should_reply 直接继承门控判断结果
        should_reply = gate.allow
        # flood 模式下只有显式触发才回复（门控已处理）
        # 回复风格
        reply_style = "gentle" if emotional else "short" if low_info else "normal"
        if parsed.is_group and gate.group_mode in {"hot", "flood"}:
            reply_style = "short"
        return IntentDecision(
            should_reply=should_reply,
            reply_style=reply_style,
            need_reason_model=reasoning or emotional,
            need_emoji=bool(parsed.is_group and gate.is_triggered and not low_info),
            confidence=0.35,
            reply_length="short" if low_info else "normal",
            notes="heuristic",
        )

    def _build_prompt(
        self,
        parsed: ParsedMessage,
        gate: GateDecision,
        fallback: IntentDecision,
    ) -> list[dict[str, str]]:
        """构建意图判断的 LLM 提示词。

        Args:
            parsed: 解析后的消息对象
            gate: 门控决策对象
            fallback: 启发式决策结果，作为 LLM 的参考

        Returns:
            消息列表，包含系统提示和用户消息
        """
        system = (
            "你是轻量意图路由器，只输出 JSON。"
            "判断当前消息是否值得回复、建议回复风格、是否需要 reason 模型、是否需要表情。"
            "不要生成回复正文。"
        )
        user = {
            "chat_key": parsed.chat_key,
            "scope_type": parsed.scope_type,
            "scope_id": parsed.scope_id,
            "is_private": parsed.is_private,
            "is_group": parsed.is_group,
            "group_mode": gate.group_mode,
            "triggered": gate.is_triggered,
            "text": parsed.text,
            "mentioned_bot": parsed.mentioned_bot,
            "replied_to_bot": parsed.replied_to_bot,
            "question_mark": any(token in parsed.text for token in self.config.safety.question_mark_tokens),
            "low_info": len(parsed.text.strip()) <= self.config.safety.low_info_max_chars,
            "fallback": {
                "should_reply": fallback.should_reply,
                "reply_style": fallback.reply_style,
                "need_reason_model": fallback.need_reason_model,
                "need_emoji": fallback.need_emoji,
            },
            "output_schema": {
                "should_reply": "bool",
                "reply_style": "short|normal|gentle|playful|serious",
                "need_reason_model": "bool",
                "need_emoji": "bool",
                "confidence": "0-1 number",
                "reply_length": "short|normal|long",
                "notes": "short string",
            },
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]

    def _merge(self, fallback: IntentDecision, payload: dict[str, object]) -> IntentDecision:
        """合并启发式决策和 LLM 返回的决策结果。

        Args:
            fallback: 启发式决策结果（作为默认值）
            payload: LLM 返回的 JSON 对象

        Returns:
            合并后的意图决策对象，LLM 结果优先，缺失字段使用启发式值
        """
        should_reply = _bool(payload.get("should_reply"), fallback.should_reply)
        reply_style = str(payload.get("reply_style") or fallback.reply_style)
        need_reason_model = _bool(payload.get("need_reason_model"), fallback.need_reason_model)
        need_emoji = _bool(payload.get("need_emoji"), fallback.need_emoji)
        confidence = _float(payload.get("confidence"), fallback.confidence)
        reply_length = str(payload.get("reply_length") or fallback.reply_length)
        notes = str(payload.get("notes") or fallback.notes)
        return IntentDecision(
            should_reply=should_reply,
            reply_style=reply_style,
            need_reason_model=need_reason_model,
            need_emoji=need_emoji,
            confidence=confidence,
            reply_length=reply_length,
            notes=notes,
        )


def _bool(value: object, default: bool) -> bool:
    """将值转换为布尔类型，如果值本身是布尔类型则直接返回，否则使用默认值。

    Args:
        value: 待转换的值
        default: 默认布尔值

    Returns:
        布尔值
    """
    if isinstance(value, bool):
        return value
    return default


def _float(value: object, default: float) -> float:
    """将值转换为浮点数类型，如果值本身是数值类型则转换为浮点数，否则使用默认值。

    Args:
        value: 待转换的值
        default: 默认浮点数值

    Returns:
        浮点数值
    """
    if isinstance(value, (int, float)):
        return float(value)
    return default
