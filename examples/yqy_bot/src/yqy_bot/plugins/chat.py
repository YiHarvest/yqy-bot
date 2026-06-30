"""YQY 聊天机器人插件。

精简版：只保留消息路由和回复发送核心逻辑。
配置、提示词构建、事实闸门、表情指令等已拆分到 services 子模块。
"""

from __future__ import annotations

import os
import sys
import time
import random
from typing import Any

from iamai import (
    Context,
    Event,
    LLMClient,
    LLMConfig,
    Plugin,
    message_handler,
    middleware,
)
from iamai.config import load_env_file
from loguru import logger

# 配置服务
from services.config_service import (
    GROUP_REPLY_BASE_PROBABILITY,
    GROUP_REPLY_COOLDOWN_SECONDS,
    GROUP_REPLY_ENABLED,
    GROUP_REPLY_MAX_PER_MINUTE,
    GROUP_REPLY_TRIGGER_KEYWORDS,
    MAX_HISTORY_TURNS,
    MAX_REFLECTIONS_PER_DAY,
    MESSAGES_BEFORE_REFLECTION,
    PROJECT_ROOT,
)

# 事实闸门
from services.fact_guard import (
    build_evidence_text,
    check_toxic,
    check_unsupported_claims,
    get_toxic_fallback,
    parse_result,
    rewrite_unsafe_reply,
)

# 提示词构建
from services.prompt_builder import PromptBuilder, build_user_message_with_time

# 表情指令
from services.meme_commands import MemeCommandHandler

# 数据库
from services.db import init_db

# 情绪服务
from services.mood import (
    NEGATIVE_KEYWORDS,
    POSITIVE_KEYWORDS,
    MoodService,
    REACTION_LONELINESS_DELTA,
    REACTION_NEGATIVE_MOOD_DELTA,
    REACTION_POSITIVE_MOOD_DELTA,
    REPLY_ENERGY_DELTA,
)

# 关系服务
from services.relation import (
    NEGATIVE_FAVORABILITY_DELTA,
    POSITIVE_FAVORABILITY_DELTA,
    RELATION_NEGATIVE,
    RELATION_POSITIVE,
    REPLY_INTIMACY_DELTA,
    RelationshipService,
)

# 其他服务
from services.history import HistoryService
from services.memory import MemoryService
from services.meme_service import MemeService
from services.persona import PersonaService
from services.reflection import ReflectionService
from services.social_memory import SocialMemoryService
from services.behavior import BehaviorService
from services.reasoning import RelationshipReasoningService

# 辅助模块（从 config_service 导入）
from services.config_service import MIN_TEXT_LENGTH, QUESTION_MARKS, SKIP_KEYWORDS
from services.human_behavior import send_human_like as _send_human
from services.config_service import MAX_REFLECTIONS_PER_DAY

# 表情包配文策略：有图时默认不发文字
MEME_CAPTION_MODE: str = "never"

# 环境初始化
sys.path.insert(0, str(PROJECT_ROOT))
load_env_file(PROJECT_ROOT / ".env")


def _is_media_segment(seg: dict[str, Any]) -> bool:
    """判断消息段是否为媒体类型。"""
    seg_kind = seg.get("kind", "")
    if seg_kind == "mface":
        return True
    data_keys = set(seg.get("data", {}).keys())
    if data_keys & {"file", "url"}:
        return True
    if data_keys & {"chainCount", "resultId"}:
        return True
    return False


def _should_extract_memory(text: str) -> bool:
    """规则过滤层：仅值得记忆的消息才进入 LLM 提炼。"""
    if not text or len(text) < MIN_TEXT_LENGTH:
        return False
    if text.isdigit():
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    if text in SKIP_KEYWORDS:
        return False
    if any(mark in text for mark in QUESTION_MARKS):
        return False
    return True


def _log_reply(text: str, face_id: str, send_meme: bool, meme_url: str | None) -> None:
    """记录回复内容。"""
    parts = []
    if send_meme:
        parts.append("图片" + (f"[{meme_url[:40]}]" if meme_url else "[拉取失败]"))
    if text:
        parts.append(f"文字[{text[:40]}]")
    if face_id:
        parts.append(f"表情[{face_id}]")
    label = " + ".join(parts) if parts else "空"
    logger.info(f"AI回复: {label}")


class YqyChatPlugin(Plugin):
    """YQY 聊天机器人插件。"""

    name = "yqy_chat"
    description = "YQY 的个人聊天机器人。"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_db()
        # 服务初始化
        self._history = HistoryService()
        self._memory = MemoryService()
        self._mood = MoodService()
        self._relation = RelationshipService()
        self._behavior = BehaviorService()
        self._meme = MemeService()
        self._persona = PersonaService()
        self._reflection = ReflectionService()
        self._social = SocialMemoryService()
        self._reasoning = RelationshipReasoningService()
        self._prompt_builder: PromptBuilder | None = None
        self._meme_handler: MemeCommandHandler | None = None
        # 状态
        self._msg_counter: int = 0
        self._meme_streak: int = 0
        self._initialized: bool = False
        # 群聊回复状态追踪
        self._group_last_reply_time: dict[str, float] = {}  # group_id -> timestamp
        self._group_reply_count: dict[str, list[float]] = {}  # group_id -> [timestamps]

    async def startup(self) -> None:
        """插件启动时初始化服务。"""
        self._prompt_builder = PromptBuilder(
            mood_service=self._mood,
            relation_service=self._relation,
            persona_service=self._persona,
            memory_service=self._memory,
            reflection_service=self._reflection,
            social_memory_service=self._social,
        )
        self._meme_handler = MemeCommandHandler(self._meme)
        self._initialized = True
        logger.info("YqyChatPlugin 启动完成")

    # ═══════════════════════════════════════════
    #  辅助方法
    # ═══════════════════════════════════════════

    def _proactive_users(self) -> set[str]:
        """获取主动聊天目标用户列表。"""
        raw = os.getenv("YQY_BOT_PROACTIVE_USERS", "2510989916,3403565936")
        return {x.strip() for x in raw.replace("，", ",").split(",") if x.strip()}

    def _event_raw(self, event: Event) -> dict:
        """获取事件的原始数据。"""
        raw = getattr(event, "raw", None) or getattr(event, "payload", None) or {}
        return raw if isinstance(raw, dict) else {}

    def _is_group_event(self, event: Event) -> bool:
        """判断是否为群聊事件。"""
        raw = self._event_raw(event)
        return raw.get("message_type") == "group" or raw.get("group_id") is not None

    def _group_id(self, event: Event) -> str:
        """获取群聊 ID。"""
        raw = self._event_raw(event)
        return str(raw.get("group_id") or getattr(event, "channel_id", "") or "")

    def _is_at_me_event(self, event: Event) -> bool:
        """判断是否为 @ 机器人事件。"""
        raw = self._event_raw(event)
        self_id = str(raw.get("self_id") or raw.get("bot_id") or "")
        message = raw.get("message") or raw.get("message_segments") or []

        if isinstance(message, list):
            for seg in message:
                if not isinstance(seg, dict):
                    continue
                if seg.get("type") != "at":
                    continue
                qq = str((seg.get("data") or {}).get("qq") or "")
                if qq == self_id or qq == "all":
                    return True
        return False

    def _get_message_text(self, event: Event) -> str:
        """获取消息的纯文本内容。"""
        raw = self._event_raw(event)
        message = raw.get("message") or raw.get("message_segments") or []

        if isinstance(message, str):
            return message.strip()

        if isinstance(message, list):
            texts = []
            for seg in message:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    text = (seg.get("data") or {}).get("text", "")
                    if text:
                        texts.append(text)
            return " ".join(texts).strip()

        return ""

    def _should_reply_group_message(self, event: Event) -> bool:
        """智能判断群聊消息是否应该回复。

        判断逻辑：
        1. 被 @ 必定回复
        2. 包含触发关键词提高回复概率
        3. 基础概率回复
        4. 考虑冷却时间和频率限制
        """
        # 1. 被 @ 必定回复
        if self._is_at_me_event(event):
            return True

        # 如果群聊回复功能未启用，只响应 @
        if not GROUP_REPLY_ENABLED:
            return False

        group_id = self._group_id(event)
        now = time.time()

        # 2. 检查冷却时间（距离上次回复的时间）
        last_reply = self._group_last_reply_time.get(group_id, 0)
        if now - last_reply < GROUP_REPLY_COOLDOWN_SECONDS:
            return False

        # 3. 检查每分钟回复次数限制
        reply_times = self._group_reply_count.get(group_id, [])
        # 清理超过1分钟的记录
        reply_times = [t for t in reply_times if now - t < 60]
        self._group_reply_count[group_id] = reply_times

        if len(reply_times) >= GROUP_REPLY_MAX_PER_MINUTE:
            return False

        # 4. 获取消息文本，检查触发关键词
        text = self._get_message_text(event)
        has_keyword = any(kw in text for kw in GROUP_REPLY_TRIGGER_KEYWORDS)

        # 5. 决定是否回复
        # 如果包含关键词，概率提高 2 倍
        probability = GROUP_REPLY_BASE_PROBABILITY
        if has_keyword:
            probability = min(probability * 2, 1.0)

        should_reply = random.random() < probability

        # 如果决定回复，更新状态
        if should_reply:
            self._group_last_reply_time[group_id] = now
            self._group_reply_count[group_id].append(now)

        return should_reply

    def _should_handle_event(self, event: Event) -> bool:
        """判断是否应处理该事件。"""
        # 私聊：谁主动找它，它都可以回复
        if not self._is_group_event(event):
            return True
        # 群聊：智能判断是否回复
        return self._should_reply_group_message(event)

    def _should_record_source_for_active_chat(self, event: Event) -> bool:
        """判断是否应记录主动聊天源。"""
        if self._is_group_event(event):
            return False
        user_id = str(getattr(event, "user_id", "") or "")
        return user_id in self._proactive_users()

    def _reply_target(self, ctx: Context, user_id: str) -> dict:
        """确定回复目标。"""
        if self._is_group_event(ctx.event):
            group_id = self._group_id(ctx.event)
            if group_id:
                return {"group_id": group_id}
        return {"user_id": user_id}

    def _get_session_id(self, event: Event) -> str:
        """获取 session_id，用于区分不同的聊天会话。

        私聊：直接用 user_id
        群聊：用 group_id:user_id 组合，避免不同群成员的历史混淆
        """
        user_id = str(getattr(event, "user_id", "") or "default")
        if self._is_group_event(event):
            group_id = self._group_id(event)
            return f"{group_id}:{user_id}"
        return user_id

    # ═══════════════════════════════════════════
    #  情绪/关系调整
    # ═══════════════════════════════════════════

    def _adjust_mood_from_text(self, text: str) -> None:
        """根据消息内容调整情绪。"""
        if not text:
            return
        self._mood.adjust_loneliness(REACTION_LONELINESS_DELTA)
        if any(kw in text for kw in POSITIVE_KEYWORDS):
            self._mood.adjust_mood(REACTION_POSITIVE_MOOD_DELTA)
        if any(kw in text for kw in NEGATIVE_KEYWORDS):
            self._mood.adjust_mood(REACTION_NEGATIVE_MOOD_DELTA)

    def _adjust_relation_from_text(self, text: str, user_id: str) -> None:
        """根据消息内容调整关系。"""
        if not text:
            return
        if any(kw in text for kw in RELATION_POSITIVE):
            self._relation.adjust_favorability(user_id, POSITIVE_FAVORABILITY_DELTA)
        if any(kw in text for kw in RELATION_NEGATIVE):
            self._relation.adjust_favorability(user_id, NEGATIVE_FAVORABILITY_DELTA)

    # ═══════════════════════════════════════════
    #  记忆提取
    # ═══════════════════════════════════════════

    async def _extract_memory(self, user_message: str) -> dict[str, Any]:
        """从用户消息中提取值得长期记住的事实。"""
        config = LLMConfig.from_mapping()
        prompt = (
            "分析用户消息，判断是否包含值得长期记住的事实。\n\n"
            "## 保存（save=true）：\n"
            "- 身份信息（名字、年龄、职业）\n"
            "- 兴趣爱好、擅长领域\n"
            "- 工作/学习相关的重要事件\n"
            "- 长期计划或目标\n"
            "- 家庭成员信息\n"
            "- 重要日期或事件\n\n"
            "## 不保存（save=false）：\n"
            "- 日常问候、寒暄\n"
            "- 表情包、语气词\n"
            "- 临时性的闲聊\n"
            "- 没有实质性信息的对话\n\n"
            "## 重要：记忆内容必须严格基于用户消息原文，禁止自行补充或编造细节。\n"
            '返回 JSON：{"save": true/false, "memory": "提炼的事实", "importance": 0.1~1.0}\n'
            "用户消息：" + user_message
        )
        try:
            return await LLMClient(config).chat_json(
                [{"role": "user", "content": prompt}]
            )
        except Exception:
            return {"save": False}

    async def _extract_social_memory(self, user_message: str) -> dict[str, Any]:
        """从用户消息中提取社交关系记忆。"""
        config = LLMConfig.from_mapping()
        prompt = (
            "分析用户消息，判断是否提到了其他人物关系。\n\n"
            '返回 JSON：{"save": true/false, "target_user": "被提到的人", '
            '"relation": "关心/观察/吐槽/帮助", "content": "关系描述", "importance": 0.1~1.0}\n\n'
            "关系描述要求：简洁概括两人之间的互动或关系，禁止编造。\n"
            "用户消息：" + user_message
        )
        try:
            return await LLMClient(config).chat_json(
                [{"role": "user", "content": prompt}]
            )
        except Exception:
            return {"save": False}

    # ═══════════════════════════════════════════
    #  反思生成
    # ═══════════════════════════════════════════

    async def _generate_reflection(self, user_id: str) -> None:
        """触发反思。"""
        if self._reflection.count_today() >= MAX_REFLECTIONS_PER_DAY:
            return

        memories = self._memory.get_memories(user_id)
        history = self._history.get_recent_history(user_id, MAX_HISTORY_TURNS)
        rel = self._relation.get_or_create_user(user_id)

        memory_text = "\n".join(f"- {m}" for m in memories) or "（暂无长期记忆）"
        history_text = (
            "\n".join(f"{h['role']}: {h['content']}" for h in history)
            or "（暂无最近聊天记录）"
        )

        config = LLMConfig.from_mapping()
        prompt = (
            f"你是 YHarvest，你在和 {rel['nickname']}（身份：{rel['identity']}）聊天。\n\n"
            f"## 你记得的事实：\n{memory_text}\n\n"
            f"## 最近聊天：\n{history_text}\n\n"
            "基于以上信息，提炼一条你的「观察/判断」。要求：\n"
            "- 是总结和判断，不是事实复述\n"
            "- 禁止编造不存在的事件\n"
            '- 如果信息不足，返回 {"save": false}\n\n'
            '返回 JSON：{"save": true/false, "reflection": "观察内容", "importance": 0.1~1.0}'
        )
        try:
            result = await LLMClient(config).chat_json(
                [{"role": "user", "content": prompt}]
            )
            if result.get("save") and result.get("reflection"):
                self._reflection.save_reflection(
                    str(result["reflection"]), float(result.get("importance", 0.5))
                )
                logger.info(f"反思保存: {result['reflection'][:60]}")
        except Exception:
            logger.warning("反思生成失败")

    # ═══════════════════════════════════════════
    #  中间件
    # ═══════════════════════════════════════════

    @middleware(priority=5, phase="before")
    async def record_source(self, ctx: Context) -> None:
        """记录主动聊天源。"""
        if not self._should_record_source_for_active_chat(ctx.event):
            return
        ctx.shared_state["last_user"] = ctx.event.user_id
        ctx.shared_state["last_channel"] = ctx.event.channel_id

    # ═══════════════════════════════════════════
    #  收藏表情指令（优先级 95）
    # ═══════════════════════════════════════════

    @message_handler(priority=95)
    async def handle_meme_commands(self, ctx: Context, event: Event) -> None:
        """处理收藏表情指令。"""
        if not self._should_handle_event(event):
            return
        if ctx.shared_state.get("meme_handled"):
            return

        text = ctx.text.strip()
        user_id = event.user_id or "default"
        segments = event.message.segments if hasattr(event.message, "segments") else []
        reply_target = self._reply_target(ctx, user_id)

        handled = await self._meme_handler.handle_command(
            ctx, user_id, text, segments, reply_target
        )
        if handled:
            ctx.shared_state["meme_handled"] = True

    # ═══════════════════════════════════════════
    #  主聊天逻辑（优先级 90）
    # ═══════════════════════════════════════════

    @message_handler(priority=90)
    async def free_chat(self, ctx: Context, event: Event) -> None:
        """主聊天处理。"""
        if not self._should_handle_event(event):
            return
        if ctx.shared_state.get("meme_handled"):
            return

        # 时间衰减
        self._mood.tick()

        segments = event.message.segments
        text = ctx.text.strip()
        has_media = any(_is_media_segment(s) for s in segments)

        if has_media and not text:
            user_msg = "对方发了一张图片/表情包过来"
            self._meme_streak += 1
        elif not text or text.startswith("/"):
            return
        else:
            user_msg = text
            self._meme_streak = 0

        self._adjust_mood_from_text(text)

        # session_id 区分聊天会话：私聊用 user_id，群聊用 group_id:user_id
        session_id = self._get_session_id(event)
        # user_id 始终是实际发送者（群聊时从 session_id 提取）
        user_id = event.user_id or (
            session_id.split(":")[-1] if ":" in session_id else "default"
        )

        self._relation.get_or_create_user(user_id)
        self._relation.update_last_chat_time(user_id)
        self._adjust_relation_from_text(text, user_id)

        result = await self._ai_reply(user_msg, session_id, user_id)
        # 确定回复目标（群聊发到 group_id，私聊发到 user_id）
        reply_target = self._reply_target(ctx, user_id)
        await self._send_reply(ctx, result, session_id, user_id, reply_target)

    async def _ai_reply(
        self, user_message: str, session_id: str, user_id: str
    ) -> dict[str, Any]:
        """调用 LLM 生成回复。"""
        config = LLMConfig.from_mapping()
        system_prompt = self._prompt_builder.build(user_id)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._history.get_recent_history(session_id, MAX_HISTORY_TURNS))
        messages.append(
            {"role": "user", "content": build_user_message_with_time(user_message)}
        )

        try:
            return await LLMClient(config).chat_json(messages)
        except Exception:
            text = await LLMClient(config).chat_text(messages)
            return {"text": text, "face_id": "", "send_meme": False}

    async def _send_reply(
        self,
        ctx: Context,
        result: dict[str, Any] | list[Any],
        session_id: str,
        user_id: str,
        reply_target: dict[str, str] | None = None,
    ) -> None:
        """发送回复（含毒性过滤、事实闸门、表情包处理）。

        Args:
            ctx: 上下文对象
            result: LLM 生成的回复结果
            session_id: 会话 ID
            user_id: 用户 ID
            reply_target: 发送目标，如 {"user_id": "xxx"} 或 {"group_id": "xxx"}
                          如果为 None，会根据事件类型自动确定
        """
        text, face_id, send_meme = parse_result(result)

        # 毒性过滤
        toxic_match = check_toxic(text)
        if toxic_match:
            logger.warning(f"毒性拦截: text={text[:50]} pattern={toxic_match}")
            fallback = get_toxic_fallback()
            text, face_id, send_meme = (
                fallback["text"],
                fallback.get("face_id", ""),
                fallback.get("send_meme", False),
            )

        # 事实闸门
        user_raw = ctx.text.strip()
        if text and user_raw:
            evidence = build_evidence_text(
                current_user_message=user_raw,
                user_history_messages=self._history.get_recent_user_messages(
                    session_id, limit=20
                ),
                memories=self._memory.get_memories(user_id, limit=20),
                social_memories=[
                    m["content"]
                    for m in self._social.get_related_memories(user_id, limit=10)
                ],
            )
            unsupported = check_unsupported_claims(text, evidence)
            if unsupported:
                logger.warning(f"事实闸门拦截: unsupported={unsupported}")
                rewritten = await rewrite_unsafe_reply(text, unsupported, user_raw)
                text, face_id, send_meme = parse_result(rewritten)
                # 重写后再检查毒性
                toxic2 = check_toxic(text)
                if toxic2:
                    fallback = get_toxic_fallback()
                    text, face_id, send_meme = fallback["text"], "", False

        # 表情包反击
        if self._meme_streak >= 2:
            send_meme = True
            logger.info(f"表情包反击: streak={self._meme_streak}")

        # 行为决策 → 获取情绪分类
        decision = self._behavior.decide_next_action(
            self._mood.get_state(),
            self._relation.get_or_create_user(user_id),
            self._memory.get_memories(user_id),
        )
        emotion = decision.get("emotion", "teasing")

        # 按情绪拉取表情包
        meme_url: str | None = None
        meme_mface_data: dict[str, Any] | None = None
        if send_meme:
            adapters = getattr(self.runtime, "adapters", [])
            if adapters:
                fav = await self._meme.get_meme_url(
                    adapters[0], emotion, user_id=user_id
                )
                if fav:
                    meme_url = fav["url"]  # 现在统一返回 image 类型

        # 表情包配文策略
        if (meme_url or meme_mface_data) and text:
            if MEME_CAPTION_MODE == "never":
                text = ""
            elif MEME_CAPTION_MODE == "short" and len(text.strip()) > 8:
                text = ""

        # 记录历史
        if text or face_id or meme_url or meme_mface_data:
            _log_reply(text, face_id, send_meme, meme_url or "[mface]")
            self._history.append_turn(
                session_id, user_raw or "[图片]", text or "[表情包]"
            )

        # 真人化发送
        adapters = getattr(self.runtime, "adapters", [])
        if adapters:
            # 如果未指定 reply_target，根据事件类型自动确定
            if reply_target is None:
                reply_target = self._reply_target(ctx, user_id)

            await _send_human(
                adapter=adapters[0],
                user_id=user_id,
                text=text,
                meme_url=meme_url,
                meme_mface_data=meme_mface_data,
                face_id=face_id,
                event_message=(
                    ctx.event.message if hasattr(ctx.event, "message") else None
                ),
                target=reply_target,
            )

        # 记忆提取
        if (
            user_raw
            and user_raw != "对方发了一张图片/表情包过来"
            and _should_extract_memory(user_raw)
        ):
            memory = await self._extract_memory(user_raw)
            if memory.get("save") and memory.get("memory"):
                self._memory.save_memory(
                    user_id, memory["memory"], importance=memory.get("importance", 0.5)
                )
                logger.info(f"记忆保存: {memory['memory'][:40]}")

                social = await self._extract_social_memory(user_raw)
                if social.get("save") and social.get("content"):
                    self._social.save_social_memory(
                        subject_user=user_id,
                        target_user=str(social.get("target_user", "某人")),
                        relation=str(social.get("relation", "观察")),
                        content=str(social["content"]),
                        importance=float(social.get("importance", 0.5)),
                    )

        # 状态更新
        self._mood.adjust_energy(REPLY_ENERGY_DELTA)
        self._relation.adjust_intimacy(user_id, REPLY_INTIMACY_DELTA)

        # 反思触发
        self._msg_counter += 1
        if self._msg_counter >= MESSAGES_BEFORE_REFLECTION:
            self._msg_counter = 0
            await self._generate_reflection(user_id)
            await self._reasoning.generate_reasoning(user_id)
