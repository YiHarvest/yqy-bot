from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yqy_bot.background.worker import BackgroundWorker
from yqy_bot.context.builder import ContextBuilder
from yqy_bot.core.config import ProjectConfig, load_project_config
from yqy_bot.core.cooldown import GroupHeat, SendCooldown
from yqy_bot.core.llm_router import LLMRouter
from yqy_bot.core.models import (
    GateDecision,
    GeneratedResponse,
    IntentDecision,
    ParsedMessage,
    SearchResult,
)
from yqy_bot.intent.router import IntentRouter
from yqy_bot.qq.emoji import EmojiService
from yqy_bot.qq.human import HumanBehavior
from yqy_bot.qq.napcat_client import NapCatClient
from yqy_bot.qq.napcat_tools import NapCatTools
from yqy_bot.qq.parser import parse_message_input
from yqy_bot.response.generator import ResponseGenerator
from yqy_bot.safety.guard import SafetyGuard
from yqy_bot.storage.database import Database
from yqy_bot.storage.repositories import Repositories
from yqy_bot.tools.search_policy import decide_search
from yqy_bot.tools.search_mcp import SearchMCPClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PipelineBundle:
    config: ProjectConfig
    database: Database
    repos: Repositories
    llm_router: LLMRouter
    context_builder: ContextBuilder
    intent_router: IntentRouter
    response_generator: ResponseGenerator
    safety_guard: SafetyGuard
    emoji_service: EmojiService
    background: BackgroundWorker
    send_cooldown: SendCooldown
    group_heat: GroupHeat
    human_behavior: HumanBehavior
    napcat_client: NapCatClient
    napcat_tools: NapCatTools
    search_mcp_client: SearchMCPClient | None = None


@dataclass(slots=True)
class PipelineResult:
    parsed: ParsedMessage
    gate: GateDecision
    intent: IntentDecision
    response: GeneratedResponse | None
    assistant_written: bool
    sent: bool


class ChatPipeline:
    def __init__(self, bundle: PipelineBundle) -> None:
        """初始化聊天管道。

        Args:
            bundle: 管道依赖包，包含所有必要的服务实例
        """
        self.bundle = bundle

    @classmethod
    def from_runtime(cls, runtime_root: Path) -> "ChatPipeline":
        """从运行时根目录创建聊天管道实例。

        Args:
            runtime_root: 项目运行时根目录路径

        Returns:
            配置完成的 ChatPipeline 实例
        """
        config = load_project_config(runtime_root)
        return cls.from_config(config)

    @classmethod
    def from_config(cls, config: ProjectConfig) -> "ChatPipeline":
        """从配置对象创建聊天管道实例。

        Args:
            config: 项目配置对象

        Returns:
            配置完成的 ChatPipeline 实例，包含所有依赖服务
        """
        database = Database(config.database_path)
        repos = Repositories(database)
        llm_router = LLMRouter()
        napcat_client = NapCatClient(
            base_url=config.bot.napcat.http_base_url,
            access_token=config.bot.napcat.access_token,
            timeout=config.bot.napcat.timeout_seconds,
        )
        napcat_tools = NapCatTools(client=napcat_client)
        context_builder = ContextBuilder(
            config=config, repos=repos, napcat_tools=napcat_tools
        )
        intent_router = IntentRouter(config=config, llm_router=llm_router)
        response_generator = ResponseGenerator(config=config, llm_router=llm_router)
        safety_guard = SafetyGuard(config=config, llm_router=llm_router)
        emoji_service = EmojiService(
            repos=repos,
            napcat=napcat_tools,
            custom_face_count=config.bot.napcat.custom_face_count,
        )
        background = BackgroundWorker(config=config, repos=repos, llm_router=llm_router)
        send_cooldown = SendCooldown(config=config, repos=repos)
        group_heat = GroupHeat(config=config, repos=repos)
        human_behavior = HumanBehavior()
        # 创建搜索 MCP 客户端（如果启用）
        search_mcp_client: SearchMCPClient | None = None
        if config.bot.search_mcp.enabled:
            search_mcp_client = SearchMCPClient(settings=config.bot.search_mcp)
        return cls(
            PipelineBundle(
                config=config,
                database=database,
                repos=repos,
                llm_router=llm_router,
                context_builder=context_builder,
                intent_router=intent_router,
                response_generator=response_generator,
                safety_guard=safety_guard,
                emoji_service=emoji_service,
                background=background,
                send_cooldown=send_cooldown,
                group_heat=group_heat,
                human_behavior=human_behavior,
                napcat_client=napcat_client,
                napcat_tools=napcat_tools,
                search_mcp_client=search_mcp_client,
            )
        )

    async def startup(self) -> None:
        """启动聊天管道，初始化后台任务处理器。"""
        await self.bundle.background.start()

    async def shutdown(self) -> None:
        """关闭聊天管道，停止后台任务并关闭数据库连接。"""
        await self.bundle.background.stop()
        self.bundle.database.close()

    async def process_message(
        self,
        payload: dict[str, Any] | ParsedMessage,
        *,
        sender: Any | None = None,
    ) -> PipelineResult:
        """处理消息的核心管道流程。

        执行完整的消息处理流程：解析消息、判断门控、
        决定意图、生成回复、安全检查、发送消息。

        Args:
            payload: 原始消息载荷字典或已解析的 ParsedMessage 对象
            sender: 发送器对象，用于发送回复消息，可选

        Returns:
            处理结果对象，包含解析后的消息、决策、回复等信息
        """
        start_time = time.time()
        parsed = (
            payload
            if isinstance(payload, ParsedMessage)
            else parse_message_input(payload)
        )
        LOGGER.info(
            "[管道] session_id=%s chat_key=%s user_id=%s is_group=%s text=%s",
            parsed.session_id,
            parsed.chat_key,
            parsed.user_id,
            parsed.is_group,
            parsed.text[:50] if parsed.text else "",
        )
        if self._is_ignored(parsed):
            LOGGER.info(
                "[管道] session_id=%s 忽略消息 reason=ignored_or_command",
                parsed.session_id,
            )
            return PipelineResult(
                parsed=parsed,
                gate=GateDecision(allow=False, group_mode="ignored", reason="ignored"),
                intent=IntentDecision(should_reply=False),
                response=None,
                assistant_written=False,
                sent=False,
            )
        self.bundle.repos.add_chat_history(
            parsed=parsed,
            role="user",
            content=parsed.text,
            metadata={"segments": parsed.segments},
        )
        now = time.time()
        # 冷却检查提前：群聊冷却未过时直接跳过门控判断
        cooldown_blocked = False
        if not self._is_superuser(parsed):
            if not self.bundle.send_cooldown.can_reply(parsed, now=now):
                cooldown_blocked = True
                LOGGER.info("[管道] session_id=%s 冷却中(提前检查)", parsed.session_id)
        group_heat_state = self.bundle.group_heat.update(parsed, now=now)
        gate = self._reply_gate(
            parsed, group_heat_state, cooldown_blocked=cooldown_blocked
        )
        LOGGER.info(
            "[管道] session_id=%s 门控 gate_allow=%s group_mode=%s group_heat=%s is_triggered=%s reason=%s is_superuser=%s cooldown_blocked=%s",
            parsed.session_id,
            gate.allow,
            gate.group_mode,
            group_heat_state,
            gate.is_triggered,
            gate.reason,
            self._is_superuser(parsed),
            cooldown_blocked,
        )
        # 冷却未过时阻止回复（superusers 跳过冷却限制）
        if cooldown_blocked and not self._is_superuser(parsed):
            gate.allow = False
            gate.reason = "send cooldown"
            gate.is_cooldown_blocked = True
            LOGGER.info("[管道] session_id=%s 冷却中 blocked=True", parsed.session_id)
        # superusers 强制允许回复
        if self._is_superuser(parsed):
            gate.allow = True
            gate.is_triggered = True
            gate.is_cooldown_blocked = False
            LOGGER.info("[管道] session_id=%s superuser强制回复", parsed.session_id)
        intent = await self.bundle.intent_router.decide(parsed, gate)
        self.bundle.repos.log_intent(parsed, gate, intent)
        if not gate.allow or not intent.should_reply:
            LOGGER.info(
                "[管道] session_id=%s 不回复 gate_allow=%s intent_should_reply=%s elapsed=%.2fs",
                parsed.session_id,
                gate.allow,
                intent.should_reply,
                time.time() - start_time,
            )
            await self.bundle.background.enqueue(parsed, gate, intent, None, None)
            return PipelineResult(
                parsed=parsed,
                gate=gate,
                intent=intent,
                response=None,
                assistant_written=False,
                sent=False,
            )
        # 搜索判断和调用（在意图判断之后、上下文构建之前）
        search_result: SearchResult | None = None
        if self.bundle.search_mcp_client is not None:
            search_decision = decide_search(
                parsed.text,
                intent,
                gate,
                enabled=self.bundle.config.bot.search_mcp.enabled,
            )
            if search_decision.need_search or search_decision.need_extract:
                LOGGER.info(
                    "[搜索工具] session_id=%s need_search=%s need_extract=%s reason=%s",
                    parsed.session_id,
                    search_decision.need_search,
                    search_decision.need_extract,
                    search_decision.reason,
                )
                try:
                    search_result = (
                        await self.bundle.search_mcp_client.search_or_extract(
                            search_decision
                        )
                    )
                except Exception as exc:
                    LOGGER.warning(
                        "[搜索工具] session_id=%s 调用失败 error=%s",
                        parsed.session_id,
                        exc,
                    )
                    search_result = SearchResult(
                        ok=False,
                        type=(
                            "web_search"
                            if search_decision.need_search
                            else "web_extract"
                        ),
                        query=search_decision.search_query
                        or search_decision.extract_url,
                        error=str(exc),
                        message="搜索失败，本轮回答不要伪造最新信息。",
                    )
        LOGGER.info(
            "[管道] session_id=%s 开始生成回复 emoji_request=%s",
            parsed.session_id,
            self.bundle.emoji_service.is_explicit_request(parsed.text),
        )
        if self.bundle.emoji_service.is_explicit_request(parsed.text):
            LOGGER.info("[管道] session_id=%s 表情请求处理", parsed.session_id)
            response = await self.bundle.emoji_service.choose_for_reply(
                intent, parsed.text
            )
            if response is not None:
                LOGGER.info("[管道] session_id=%s 表情请求有响应", parsed.session_id)
                response = response.to_response().normalized()
                response.reply_to_message_id = parsed.reply_message_id
                context = await self.bundle.context_builder.build(
                    parsed,
                    intent,
                    group_heat_state=group_heat_state,
                    search_result=search_result,
                )
                response = await self.bundle.safety_guard.check(
                    parsed, response, context
                )
                response.reply_to_message_id = (
                    response.reply_to_message_id or parsed.reply_message_id
                )
                delay_seconds = await self.bundle.human_behavior.typing_delay(
                    parsed, response.text
                )
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                if not self.bundle.send_cooldown.can_reply(parsed, now=time.time()):
                    await self.bundle.background.enqueue(
                        parsed, gate, intent, response, context
                    )
                    return PipelineResult(
                        parsed=parsed,
                        gate=gate,
                        intent=intent,
                        response=response,
                        assistant_written=False,
                        sent=False,
                    )
                sent = False
                if sender is not None:
                    await sender.send(parsed, response)
                    sent = True
                self.bundle.repos.add_chat_history(
                    parsed=parsed,
                    role="assistant",
                    content=response.text or "[emoji reply]",
                    metadata={
                        "send_face": response.send_face,
                        "face_id": response.face_id,
                        "send_mface": response.send_mface,
                        "mface": response.mface,
                        "send_image": response.send_image,
                        "image_url": response.image_url,
                        "at_user_id": response.at_user_id,
                        "reply_to_message_id": response.reply_to_message_id,
                    },
                )
                self.bundle.send_cooldown.mark_replied(
                    parsed, now=time.time(), group_heat_state=group_heat_state
                )
                LOGGER.info(
                    "[管道] session_id=%s 表情回复完成 sent=%s elapsed=%.2fs",
                    parsed.session_id,
                    sent,
                    time.time() - start_time,
                )
                await self.bundle.background.enqueue(
                    parsed, gate, intent, response, context
                )
                return PipelineResult(
                    parsed=parsed,
                    gate=gate,
                    intent=intent,
                    response=response,
                    assistant_written=True,
                    sent=sent,
                )
            LOGGER.info(
                "[管道] session_id=%s 表情请求无响应转入正常流程", parsed.session_id
            )
        LOGGER.info("[管道] session_id=%s 构建上下文", parsed.session_id)
        context = await self.bundle.context_builder.build(
            parsed,
            intent,
            group_heat_state=group_heat_state,
            search_result=search_result,
        )
        response = await self.bundle.response_generator.generate(context, intent)
        response.reply_to_message_id = (
            response.reply_to_message_id or parsed.reply_message_id
        )
        LOGGER.info("[管道] session_id=%s 安全检查", parsed.session_id)
        response = await self.bundle.safety_guard.check(parsed, response, context)
        response = await self.bundle.emoji_service.resolve_response(
            parsed.text, intent, response
        )
        response.reply_to_message_id = (
            response.reply_to_message_id or parsed.reply_message_id
        )
        delay_seconds = await self.bundle.human_behavior.typing_delay(
            parsed, response.text
        )
        LOGGER.info(
            "[管道] session_id=%s 打字延迟 delay=%.2fs",
            parsed.session_id,
            delay_seconds,
        )
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        if not self.bundle.send_cooldown.can_reply(parsed, now=time.time()):
            LOGGER.info(
                "[管道] session_id=%s 冷却阻塞(发送前) elapsed=%.2fs",
                parsed.session_id,
                time.time() - start_time,
            )
            await self.bundle.background.enqueue(
                parsed, gate, intent, response, context
            )
            return PipelineResult(
                parsed=parsed,
                gate=gate,
                intent=intent,
                response=response,
                assistant_written=False,
                sent=False,
            )
        sent = False
        if sender is not None:
            await sender.send(parsed, response)
            sent = True
        self.bundle.repos.add_chat_history(
            parsed=parsed,
            role="assistant",
            content=response.text or "[non-text reply]",
            metadata={
                "send_face": response.send_face,
                "face_id": response.face_id,
                "send_mface": response.send_mface,
                "mface": response.mface,
                "send_image": response.send_image,
                "image_url": response.image_url,
                "at_user_id": response.at_user_id,
                "reply_to_message_id": response.reply_to_message_id,
            },
        )
        self.bundle.send_cooldown.mark_replied(
            parsed, now=time.time(), group_heat_state=group_heat_state
        )
        LOGGER.info(
            "[管道] session_id=%s 发送完成 sent=%s text=%s elapsed=%.2fs",
            parsed.session_id,
            sent,
            response.text[:50] if response.text else "",
            time.time() - start_time,
        )
        await self.bundle.background.enqueue(parsed, gate, intent, response, context)
        return PipelineResult(
            parsed=parsed,
            gate=gate,
            intent=intent,
            response=response,
            assistant_written=True,
            sent=sent,
        )

    def _is_ignored(self, parsed: ParsedMessage) -> bool:
        """判断消息是否应被忽略。

        忽略条件：空消息、命令消息（以 / 开头）、
        被屏蔽的用户或群聊。

        注意：superusers 不受屏蔽限制。

        Args:
            parsed: 解析后的消息对象

        Returns:
            如果消息应被忽略则返回 True
        """
        # superusers 不受任何限制
        if self._is_superuser(parsed):
            return False
        text = parsed.text.strip()
        if not text or text.startswith("/"):
            return True
        if parsed.user_id in set(self.bundle.config.bot.blocked_user_ids):
            return True
        if parsed.is_group and parsed.scope_id in set(
            self.bundle.config.bot.blocked_group_ids
        ):
            return True
        return False

    def _is_superuser(self, parsed: ParsedMessage) -> bool:
        """判断用户是否为特权用户（superuser）。

        superusers 跳过所有限制：
        - 不受屏蔽限制
        - 不受冷却限制
        - 强制触发回复
        - 意图识别强制 should_reply=True

        Args:
            parsed: 解析后的消息对象

        Returns:
            如果是 superuser 则返回 True
        """
        return parsed.user_id in set(self.bundle.config.bot.superusers)

    def _reply_gate(
        self,
        parsed: ParsedMessage,
        group_heat_state: str = "quiet",
        cooldown_blocked: bool = False,
    ) -> GateDecision:
        """判断消息是否应触发回复门控。

        根据消息类型、触发关键词、群热度状态等条件
        决定是否允许回复。

        回复策略：
        - 私聊：基本都回复
        - 群聊 quiet：@、回复机器人、关键词、明确问题才回复
        - 群聊 active：只回复 @、回复机器人、关键词、强问题
        - 群聊 hot：只回复 @、回复机器人
        - 群聊 flood：默认不回，@ 时可短回复

        Args:
            parsed: 解析后的消息对象
            group_heat_state: 群热度状态（quiet/active/hot/flood）

        Returns:
            门控决策对象，包含是否允许回复、群模式等信息
        """
        text = parsed.text.strip()
        explicit_trigger = self._has_trigger(parsed, text)
        explicit_emoji_request = self.bundle.emoji_service.is_explicit_request(text)
        # 私聊：基本都回复（冷却阻塞时除外）
        if parsed.is_private:
            allow = self._private_allow(
                parsed, explicit_trigger, explicit_emoji_request
            )
            # 私聊冷却阻塞时也阻止回复
            if cooldown_blocked:
                allow = False
            return GateDecision(
                allow=allow,
                group_mode="private",
                reason="private" if not cooldown_blocked else "cooldown",
                should_dispatch_background=True,
                is_triggered=explicit_trigger,
                is_cooldown_blocked=cooldown_blocked,
            )
        # 群聊：根据热度状态分层处理
        group_mode = group_heat_state or "quiet"
        # 冷却阻塞时直接返回不允许
        if cooldown_blocked:
            return GateDecision(
                allow=False,
                group_mode=group_mode,
                reason="cooldown blocked",
                should_dispatch_background=True,
                is_triggered=explicit_trigger,
                is_cooldown_blocked=True,
            )
        # flood 模式：默认不回，@ 时允许短回复
        if group_mode == "flood":
            if explicit_trigger:
                return GateDecision(
                    allow=True,
                    group_mode=group_mode,
                    reason="flood @ trigger",
                    should_dispatch_background=True,
                    is_triggered=explicit_trigger,
                    is_cooldown_blocked=False,
                )
            return GateDecision(
                allow=False,
                group_mode=group_mode,
                reason="group flood",
                should_dispatch_background=True,
                is_triggered=explicit_trigger,
                is_cooldown_blocked=True,
            )
        # hot 模式：只回复 @、回复机器人、关键词
        if group_mode == "hot":
            allow = explicit_trigger or explicit_emoji_request
            return GateDecision(
                allow=allow,
                group_mode=group_mode,
                reason="hot explicit only",
                should_dispatch_background=True,
                is_triggered=explicit_trigger,
                is_cooldown_blocked=not allow,
            )
        # active 模式：@、回复机器人、关键词、强问题
        if group_mode == "active":
            has_strong_question = self._is_strong_question(text)
            allow = explicit_trigger or explicit_emoji_request or has_strong_question
            return GateDecision(
                allow=allow,
                group_mode=group_mode,
                reason="active gate",
                should_dispatch_background=True,
                is_triggered=explicit_trigger,
                is_cooldown_blocked=not allow,
            )
        # quiet 模式：@、回复机器人、关键词、明确问题
        has_question = self._has_question(text)
        allow = explicit_trigger or explicit_emoji_request or has_question
        return GateDecision(
            allow=allow,
            group_mode=group_mode,
            reason="quiet gate",
            should_dispatch_background=True,
            is_triggered=explicit_trigger,
            is_cooldown_blocked=not allow,
        )

    def _is_strong_question(self, text: str) -> bool:
        """判断是否为强问题（必须有问号）。

        强问题定义：同时包含问号和疑问词，或明确的问题格式。

        Args:
            text: 消息文本内容

        Returns:
            如果是强问题则返回 True
        """
        text = text.strip()
        # 必须有问号
        has_question_mark = any(
            token in text for token in self.bundle.config.safety.question_mark_tokens
        )
        if not has_question_mark:
            return False
        # 同时有疑问词才算强问题
        question_words = (
            "谁",
            "什么",
            "怎么",
            "为什么",
            "哪",
            "几",
            "多少",
            "干嘛",
            "如何",
            "是不是",
            "能不能",
            "可以吗",
        )
        return any(word in text for word in question_words)

    def _has_question(self, text: str) -> bool:
        """判断是否包含明确问题。

        明确问题定义：包含问号或疑问词。

        Args:
            text: 消息文本内容

        Returns:
            如果包含明确问题则返回 True
        """
        text = text.strip()
        # 包含问号
        if any(
            token in text for token in self.bundle.config.safety.question_mark_tokens
        ):
            return True
        # 包含疑问词
        question_words = (
            "谁",
            "什么",
            "怎么",
            "为什么",
            "哪",
            "几",
            "多少",
            "干嘛",
            "如何",
            "是不是",
            "能不能",
            "可以吗",
        )
        return any(word in text for word in question_words)

    def _private_allow(
        self,
        parsed: ParsedMessage,
        explicit_trigger: bool,
        explicit_emoji_request: bool,
    ) -> bool:
        """判断私聊消息是否应触发回复。

        Args:
            parsed: 解析后的消息对象
            explicit_trigger: 是否包含显式触发关键词
            explicit_emoji_request: 是否为显式表情请求

        Returns:
            如果应回复则返回 True
        """
        if explicit_trigger or explicit_emoji_request:
            return True
        text = parsed.text.strip()
        if any(
            token in text for token in self.bundle.config.safety.question_mark_tokens
        ):
            return True
        if any(
            token in text
            for token in ("谁", "什么", "怎么", "为什么", "哪", "几", "多少", "干嘛")
        ):
            return True
        if self._is_low_info(text):
            return self.bundle.config.bot.allow_private_short_reply
        return True

    def _has_trigger(self, parsed: ParsedMessage, text: str) -> bool:
        """判断消息是否包含触发关键词。

        触发条件：被 @、引用回复、包含配置的触发关键词。

        Args:
            parsed: 解析后的消息对象
            text: 消息文本内容

        Returns:
            如果满足触发条件则返回 True
        """
        keywords = (
            self.bundle.config.bot.group_trigger_keywords
            + self.bundle.config.bot.private_trigger_keywords
        )
        lowered = text.lower()
        if parsed.is_at_bot or parsed.reply_message_id:
            return True
        return any(keyword.lower() in lowered for keyword in keywords)

    def _is_reply_worthy(
        self, parsed: ParsedMessage, text: str, group_mode: str
    ) -> bool:
        """判断消息是否值得回复。

        Args:
            parsed: 解析后的消息对象
            text: 消息文本内容
            group_mode: 群热度模式

        Returns:
            如果消息值得回复则返回 True
        """
        if self._is_low_info(text):
            return (
                parsed.is_private and self.bundle.config.bot.allow_private_short_reply
            )
        # 包含问号的消息值得回复
        if any(
            token in text for token in self.bundle.config.safety.question_mark_tokens
        ):
            return True
        # 包含疑问词的消息值得回复
        if any(token in text for token in ("为什么", "什么", "几", "多少", "如何")):
            return True
        # quiet 模式下也需要有明确触发才回复（被 @、回复机器人、触发关键词）
        # 不再无条件回复所有消息
        if group_mode == "quiet":
            return parsed.is_private  # 只有私聊才无条件回复
        # active/hot 模式下，较长消息可能值得回复
        return not parsed.is_group or len(text) > 4

    def _is_low_info(self, text: str) -> bool:
        """判断消息是否为低信息量消息。

        低信息量消息：长度过短或包含低信息关键词。

        Args:
            text: 消息文本内容

        Returns:
            如果为低信息量消息则返回 True
        """
        return len(text.strip()) <= self.bundle.config.safety.low_info_max_chars or any(
            keyword in text
            for keyword in self.bundle.config.safety.low_info_skip_keywords
        )
