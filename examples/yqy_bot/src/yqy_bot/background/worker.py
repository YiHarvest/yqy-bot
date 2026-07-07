from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

from yqy_bot.core.config import ProjectConfig
from yqy_bot.core.llm_router import LLMRouter
from yqy_bot.core.models import ConversationContext, GeneratedResponse, GateDecision, IntentDecision, ParsedMessage
from yqy_bot.storage.repositories import Repositories

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BackgroundJob:
    parsed: ParsedMessage
    gate: GateDecision
    intent: IntentDecision
    response: GeneratedResponse | None
    context: ConversationContext | None


class BackgroundWorker:
    """后台任务处理器，负责批量更新记忆、画像、摘要等数据。"""

    def __init__(self, config: ProjectConfig, repos: Repositories, llm_router: LLMRouter) -> None:
        """初始化后台任务处理器。

        Args:
            config: 项目配置对象
            repos: 数据仓库对象
            llm_router: LLM 路由器对象
        """
        self.config = config
        self.repos = repos
        self.llm_router = llm_router
        self._queue: asyncio.Queue[BackgroundJob | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._closing = asyncio.Event()

    async def start(self) -> None:
        """启动后台任务处理器，创建异步任务循环。"""
        if self._task is not None:
            return
        self._closing.clear()
        self._task = asyncio.create_task(self._run(), name="yqy-bot-background-worker")

    async def stop(self) -> None:
        """停止后台任务处理器，等待当前批次完成并清理资源。"""
        self._closing.set()
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    async def enqueue(
        self,
        parsed: ParsedMessage,
        gate: GateDecision,
        intent: IntentDecision,
        response: GeneratedResponse | None,
        context: ConversationContext | None,
    ) -> None:
        """将处理任务加入后台队列，等待批量处理。

        Args:
            parsed: 解析后的消息对象
            gate: 门控决策对象
            intent: 意图决策对象
            response: 生成的回复对象，可选（未回复时为 None）
            context: 对话上下文对象，可选（未回复时为 None）
        """
        await self._queue.put(BackgroundJob(parsed=parsed, gate=gate, intent=intent, response=response, context=context))

    async def _run(self) -> None:
        """运行后台任务循环，定期批量处理队列中的任务。

        循环会按照配置的刷新间隔和批量大小处理任务，
        在停止信号发出后会处理完剩余任务再退出。
        """
        batch: list[BackgroundJob] = []
        flush_seconds = self.config.bot.background_flush_seconds
        max_batch = self.config.bot.background_batch_size
        while not self._closing.is_set():
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=flush_seconds)
            except asyncio.TimeoutError:
                job = None
            if job is None:
                if batch:
                    await self._process_batch(batch)
                    batch.clear()
                if self._closing.is_set():
                    break
                continue
            batch.append(job)
            if len(batch) >= max_batch:
                await self._process_batch(batch)
                batch.clear()
        if batch:
            await self._process_batch(batch)

    async def _process_batch(self, batch: list[BackgroundJob]) -> None:
        """批量处理后台任务，按聊天键分组并更新记忆、画像等。

        Args:
            batch: 后台任务列表，每个任务包含消息、决策、回复等信息
        """
        LOGGER.info("[后台任务] 开始批量处理 batch_size=%s", len(batch))
        grouped: dict[str, list[BackgroundJob]] = {}
        for job in batch:
            grouped.setdefault(job.parsed.chat_key, []).append(job)
        for chat_key, jobs in grouped.items():
            latest = jobs[-1]
            if latest.context is not None:
                await self._update_from_context(latest)
            else:
                self._update_from_heuristics(latest)
        LOGGER.info("[后台任务] 批量处理完成")

    async def _update_from_context(self, job: BackgroundJob) -> None:
        """使用对话上下文通过 LLM 更新记忆、画像、摘要等。

        Args:
            job: 后台任务对象，必须包含有效的对话上下文
        """
        assert job.context is not None
        parsed = job.parsed
        context = job.context
        llm_available = self.llm_router.available("background")
        should_run_llm = self._should_run_background_llm(job)
        LOGGER.info(
            "[后台任务] chat_key=%s user_id=%s 方式=%s llm_available=%s should_run_llm=%s",
            parsed.chat_key,
            parsed.user_id,
            "llm" if llm_available and should_run_llm else "heuristic",
            llm_available,
            should_run_llm,
        )
        if llm_available and should_run_llm:
            try:
                payload = await self.llm_router.chat_json(
                    "background",
                    [
                        {
                            "role": "system",
                            "content": (
                                "你负责慢速学习，只输出 JSON。"
                                "字段：user_profile, group_profile, summary, memories, reflections, emoji。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "chat_key": parsed.chat_key,
                                    "user_id": parsed.user_id,
                                    "scope_type": parsed.scope_type,
                                    "scope_id": parsed.scope_id,
                                    "current_message": parsed.text,
                                    "reply_text": job.response.text if job.response else "",
                                    "history": [
                                        {"role": item.role, "content": item.content}
                                        for item in context.recent_history
                                    ],
                                    "summary": context.summary,
                                    "persona": context.persona.identity,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    temperature=0.2,
                    max_tokens=768,
                )
                self._apply_background_payload(job, payload)
                return
            except Exception as e:
                LOGGER.info("[后台任务] chat_key=%s LLM调用失败 error=%s", parsed.chat_key, e)
        self._update_from_heuristics(job)

    def _apply_background_payload(self, job: BackgroundJob, payload: dict[str, object]) -> None:
        """应用 LLM 返回的后台更新载荷到数据库。

        Args:
            job: 后台任务对象
            payload: LLM 返回的 JSON 对象，包含 user_profile、group_profile、
                     summary、memories、reflections、emoji 等字段
        """
        parsed = job.parsed
        user_state = self.repos.get_user_profile(parsed.user_id)
        group_state = self.repos.get_group_profile(parsed.group_id) if parsed.is_group else {
            "profile": {},
            "prompt_md": "",
            "message_count_since_update": 0,
        }
        summary = str(payload.get("summary") or "").strip()
        if summary and _should_update_summary(parsed.text):
            self.repos.upsert_summary(parsed.chat_key, summary)
            LOGGER.info("[后台任务] chat_key=%s 更新摘要 summary=%s", parsed.chat_key, summary[:60])
        user_updated = False
        if self._should_update_user_profile(parsed, user_state):
            user_profile = _normalize_user_profile(_merge_payload(user_state["profile"], payload.get("user_profile")))
            user_prompt_md = _pick_text(payload.get("user_profile"), "prompt_md") or _render_profile_md(user_profile, "用户画像")
            self.repos.upsert_user_profile(parsed.user_id, user_profile, user_prompt_md, dirty_count=0)
            user_updated = True
            LOGGER.info(
                "[后台任务] user_id=%s 更新用户画像 stable_facts=%s preferences=%s recent_focus=%s",
                parsed.user_id,
                len(user_profile.get("stable_facts", [])),
                len(user_profile.get("preferences", [])),
                len(user_profile.get("recent_focus", [])),
            )
        group_updated = False
        if parsed.is_group and self._should_update_group_profile(parsed, group_state, job.context):
            group_profile = _normalize_group_profile(_merge_payload(group_state["profile"], payload.get("group_profile")))
            group_prompt_md = _pick_text(payload.get("group_profile"), "prompt_md") or _render_profile_md(group_profile, "群画像")
            self.repos.upsert_group_profile(
                parsed.group_id,
                group_profile,
                group_prompt_md,
                message_count_since_update=0,
            )
            group_updated = True
            LOGGER.info(
                "[后台任务] group_id=%s 更新群画像 group_style=%s topics=%s noise_level=%s",
                parsed.group_id,
                group_profile.get("group_style", ""),
                len(group_profile.get("common_topics", [])),
                group_profile.get("noise_level", "quiet"),
            )
        memory_count = 0
        for item in _as_list(payload.get("memories")):
            if not isinstance(item, dict):
                continue
            self.repos.add_memory(
                parsed=parsed,
                kind=str(item.get("kind", "fact")),
                content=str(item.get("content", "")),
                score=float(item.get("score", 0.6)),
            )
            memory_count += 1
        if memory_count:
            LOGGER.info("[后台任务] chat_key=%s 添加记忆 count=%s", parsed.chat_key, memory_count)
        reflection_count = 0
        for item in _as_list(payload.get("reflections")):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if content:
                self.repos.add_reflection(parsed=parsed, content=content)
                reflection_count += 1
        if reflection_count:
            LOGGER.info("[后台任务] chat_key=%s 添加反思 count=%s contents=%s", parsed.chat_key, reflection_count, [str(item.get("content", ""))[:30] for item in _as_list(payload.get("reflections")) if isinstance(item, dict)])
        for item in _as_list(payload.get("emoji")):
            if not isinstance(item, dict):
                continue
            continue

    def _should_run_background_llm(self, job: BackgroundJob) -> bool:
        """判断是否应运行后台 LLM 处理。

        Args:
            job: 后台任务对象

        Returns:
            如果满足条件则返回 True
        """
        parsed = job.parsed
        text = parsed.text.strip()
        if _is_explicit_remember(text):
            return True
        if parsed.is_private:
            state = self.repos.get_user_profile(parsed.user_id)
            return int(state.get("dirty_count", 0)) >= 5
        if not parsed.is_group:
            return False
        group_state = self.repos.get_group_profile(parsed.group_id)
        message_count = int(group_state.get("message_count_since_update", 0))
        cooldown_state = self.repos.get_cooldown_state(parsed.chat_key) or {}
        heat_state = str(job.context.group_heat_state if job.context is not None else cooldown_state.get("heat_state", "quiet"))
        return message_count >= 50 and heat_state not in {"hot", "flood"}

    def _should_update_user_profile(self, parsed: ParsedMessage, user_state: dict[str, object]) -> bool:
        """判断是否应更新用户画像。

        Args:
            parsed: 解析后的消息对象
            user_state: 当前用户状态字典

        Returns:
            如果应更新则返回 True
        """
        text = parsed.text.strip()
        if _is_explicit_remember(text):
            return True
        return int(user_state.get("dirty_count", 0)) >= 5

    def _should_update_group_profile(
        self,
        parsed: ParsedMessage,
        group_state: dict[str, object],
        context: ConversationContext | None,
    ) -> bool:
        """判断是否应更新群画像。

        Args:
            parsed: 解析后的消息对象
            group_state: 当前群状态字典
            context: 对话上下文对象，可选

        Returns:
            如果应更新则返回 True
        """
        if not parsed.is_group:
            return False
        cooldown_state = self.repos.get_cooldown_state(parsed.chat_key) or {}
        heat_state = str(context.group_heat_state if context is not None else cooldown_state.get("heat_state", "quiet"))
        if heat_state in {"hot", "flood"}:
            return False
        return int(group_state.get("message_count_since_update", 0)) >= 50

    def _update_from_heuristics(self, job: BackgroundJob) -> None:
        """使用启发式规则更新记忆、画像、摘要等（LLM 不可用时的降级方案）。

        Args:
            job: 后台任务对象
        """
        parsed = job.parsed
        text = parsed.text.strip()
        if not text:
            return
        LOGGER.info("[后台任务] chat_key=%s user_id=%s 方式=heuristic", parsed.chat_key, parsed.user_id)
        summary = self.repos.get_summary(parsed.chat_key)
        if _should_update_summary(text):
            combined = "；".join(part for part in [summary, text[:120]] if part)
            self.repos.upsert_summary(parsed.chat_key, combined[-1200:])
            LOGGER.info("[后台任务] chat_key=%s 更新摘要(heuristic) summary=%s", parsed.chat_key, combined[-60:])
        if _should_touch_profile(text):
            user_state = self.repos.get_user_profile(parsed.user_id)
            user_profile = _normalize_user_profile(user_state["profile"])
            updated_user_profile, changed = _apply_user_profile_signal(user_profile, text, parsed.message_id)
            if changed:
                dirty_count = int(user_state.get("dirty_count", 0)) + 1
                user_prompt_md = _render_profile_md(updated_user_profile, "用户画像")
                self.repos.upsert_user_profile(parsed.user_id, updated_user_profile, user_prompt_md, dirty_count=dirty_count)
                LOGGER.info(
                    "[后台任务] user_id=%s 更新用户画像(heuristic) dirty_count=%s stable_facts=%s preferences=%s",
                    parsed.user_id,
                    dirty_count,
                    len(updated_user_profile.get("stable_facts", [])),
                    len(updated_user_profile.get("preferences", [])),
                )
        if parsed.is_group:
            self.repos.bump_group_profile_message_count(parsed.group_id)
            group_state = self.repos.get_group_profile(parsed.group_id)
            if _should_touch_group_profile(text):
                group_profile = _normalize_group_profile(group_state["profile"])
                cooldown_state = self.repos.get_cooldown_state(parsed.chat_key)
                if int(group_state.get("message_count_since_update", 0)) >= 50:
                    updated_group_profile, changed = _apply_group_profile_signal(
                        group_profile,
                        text,
                        parsed.user_id,
                        parsed.message_id,
                        cooldown_state,
                    )
                    if changed:
                        group_prompt_md = _render_profile_md(updated_group_profile, "群画像")
                        self.repos.upsert_group_profile(
                            parsed.group_id,
                            updated_group_profile,
                            group_prompt_md,
                            message_count_since_update=0,
                        )
                        LOGGER.info(
                            "[后台任务] group_id=%s 更新群画像(heuristic) topics=%s noise_level=%s",
                            parsed.group_id,
                            len(updated_group_profile.get("common_topics", [])),
                            updated_group_profile.get("noise_level", "quiet"),
                        )
        if _should_store_memory(text, self.config.safety.low_info_skip_keywords):
            self.repos.add_memory(parsed=parsed, kind="fact", content=text[:180], score=0.55)
            LOGGER.info("[后台任务] chat_key=%s 添加记忆(heuristic) content=%s", parsed.chat_key, text[:60])
        if job.response is not None and job.response.text.strip():
            reflection_content = f"本轮回复风格={job.intent.reply_style}，应保持{job.intent.reply_style}。"
            self.repos.add_reflection(
                parsed=parsed,
                content=reflection_content,
            )
            LOGGER.info("[后台任务] chat_key=%s 添加反思(heuristic) content=%s", parsed.chat_key, reflection_content)
        if job.response is not None and (job.response.send_face or job.response.send_mface):
            pass


def _merge_payload(base: dict[str, object], extra: object) -> dict[str, object]:
    """合并载荷字典，处理列表字段的去重合并。

    Args:
        base: 基础载荷字典
        extra: 新增载荷数据，可以是字典或其他类型

    Returns:
        合并后的载荷字典，列表字段会去重合并，其他字段直接覆盖
    """
    merged = dict(base)
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key == "summary":
                continue
            if isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = list(dict.fromkeys([*merged.get(key, []), *value]))
            else:
                merged[key] = value
    return merged


def _pick_text(payload: object, key: str) -> str:
    """从载荷中提取指定键的文本值。

    Args:
        payload: 载荷对象，可以是字典或其他类型
        key: 键名

    Returns:
        提取的文本字符串，如果载荷不是字典或键不存在则返回空字符串
    """
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key, "")
    return str(value).strip()


def _as_list(value: object) -> list[object]:
    """将值转换为列表，如果值本身是列表则直接返回，否则返回空列表。

    Args:
        value: 待转换的值

    Returns:
        列表对象
    """
    return value if isinstance(value, list) else []


def _should_store_memory(text: str, skip_keywords: list[str]) -> bool:
    """判断文本是否值得存储为记忆（长度足够且非低信息词）。

    Args:
        text: 消息文本内容
        skip_keywords: 低信息关键词列表

    Returns:
        如果值得存储则返回 True，否则返回 False
    """
    if len(text) < 8:
        return False
    return not any(keyword in text for keyword in skip_keywords)


def _should_touch_profile(text: str) -> bool:
    """判断文本是否值得更新画像。

    Args:
        text: 消息文本内容

    Returns:
        如果值得更新画像则返回 True
    """
    if len(text) < 8:
        return False
    if _looks_like_question(text):
        return False
    low_info = ["哈哈", "哈哈哈", "在吗", "晚安", "早安", "收到", "OK", "ok", "好的", "表情"]
    if any(keyword in text for keyword in low_info):
        return False
    return bool(_profile_signal_kind(text))


def _should_update_summary(text: str) -> bool:
    """判断摘要是否应吸收这条消息。

    摘要只保留旧事实，不吸收当前改口、否定或撤回类表达。
    """
    text = text.strip()
    if not text:
        return False
    return not _looks_like_conflict_message(text)


def _looks_like_conflict_message(text: str) -> bool:
    """判断文本是否像改口、否定或撤回消息。"""
    return any(token in text for token in ["不喜欢", "不再", "改成", "改为", "别记", "别把", "不是", "取消", "改口", "相反"])


def _should_touch_group_profile(text: str) -> bool:
    """判断文本是否值得更新群画像。

    Args:
        text: 消息文本内容

    Returns:
        如果值得更新群画像则返回 True
    """
    return _should_touch_profile(text)


def _profile_signal_kind(text: str) -> str:
    """识别文本中的画像信号类型。

    Args:
        text: 消息文本内容

    Returns:
        信号类型字符串（remember/preference/project/fact/boundary），
        如果无信号则返回空字符串
    """
    if _is_explicit_remember(text):
        return "remember"
    if _looks_like_emotion(text) and not _looks_like_long_term(text):
        return ""
    if _looks_like_preference(text):
        return "preference"
    if _looks_like_project(text):
        return "project"
    if _looks_like_stable_fact(text):
        return "fact"
    if _looks_like_boundary(text):
        return "boundary"
    return ""


def _looks_like_question(text: str) -> bool:
    """判断文本是否像问题。

    Args:
        text: 消息文本内容

    Returns:
        如果包含问号或疑问词则返回 True
    """
    lowered = text.strip()
    if not lowered:
        return False
    question_tokens = ["?", "？", "吗", "呢", "为什么", "怎么", "能不能", "可以吗", "多少", "哪个", "是否"]
    return lowered.endswith(("?", "？")) or any(token in lowered for token in question_tokens)


def _is_explicit_remember(text: str) -> bool:
    """判断文本是否包含显式的记忆请求。

    Args:
        text: 消息文本内容

    Returns:
        如果包含"记住"关键词则返回 True
    """
    return "记住" in text or "帮我记住" in text


def _looks_like_emotion(text: str) -> bool:
    """判断文本是否表达情绪。

    Args:
        text: 消息文本内容

    Returns:
        如果包含情绪词汇则返回 True
    """
    emotion_tokens = ["开心", "难过", "生气", "焦虑", "emo", "累", "烦", "郁闷", "激动", "委屈"]
    return any(token in text for token in emotion_tokens)


def _looks_like_long_term(text: str) -> bool:
    """判断文本是否描述长期状态。

    Args:
        text: 消息文本内容

    Returns:
        如果包含长期状态词汇则返回 True
    """
    tokens = ["一直", "长期", "经常", "习惯", "从来", "总是"]
    return any(token in text for token in tokens)


def _looks_like_preference(text: str) -> bool:
    """判断文本是否表达偏好。

    Args:
        text: 消息文本内容

    Returns:
        如果包含偏好词汇且非问题则返回 True
    """
    preference_tokens = ["喜欢", "不喜欢", "讨厌", "偏好", "更爱", "更喜欢", "想要", "爱吃", "爱用"]
    return any(token in text for token in preference_tokens) and not _looks_like_question(text)


def _looks_like_project(text: str) -> bool:
    """判断文本是否描述项目或工作。

    Args:
        text: 消息文本内容

    Returns:
        如果包含项目词汇且非问题则返回 True
    """
    project_tokens = ["最近", "正在", "重构", "开发", "项目", "工作", "学习", "写", "做", "维护"]
    return any(token in text for token in project_tokens) and not _looks_like_question(text)


def _looks_like_stable_fact(text: str) -> bool:
    """判断文本是否描述稳定事实。

    Args:
        text: 消息文本内容

    Returns:
        如果包含事实描述词汇且非问题则返回 True
    """
    fact_tokens = ["我是", "我叫", "我住", "我在", "我用", "我负责", "我做", "我的", "账号", "身份"]
    return any(token in text for token in fact_tokens) and not _looks_like_question(text)


def _looks_like_boundary(text: str) -> bool:
    """判断文本是否表达边界或拒绝。

    Args:
        text: 消息文本内容

    Returns:
        如果包含边界词汇且非问题则返回 True
    """
    boundary_tokens = ["不要", "别", "不想", "不喜欢", "拒绝", "不能", "禁止"]
    return any(token in text for token in boundary_tokens) and not _looks_like_question(text)


def _safe_tail(text: str, limit: int = 120) -> str:
    """截取文本尾部并限制长度。

    Args:
        text: 消息文本内容
        limit: 最大长度，默认 120

    Returns:
        截取后的文本
    """
    text = text.strip()
    return text if len(text) <= limit else text[:limit]


def _profile_list(profile: dict[str, object], key: str) -> list[str]:
    """从画像中提取列表字段。

    Args:
        profile: 画像字典
        key: 字段键名

    Returns:
        列表值，如果字段不存在或类型错误则返回空列表
    """
    value = profile.get(key, [])
    return value if isinstance(value, list) else []


def _normalize_user_profile(profile: dict[str, object]) -> dict[str, object]:
    """规范化用户画像格式。

    Args:
        profile: 原始画像字典

    Returns:
        规范化后的画像字典，包含标准字段
    """
    normalized = {
        "stable_facts": _profile_list(profile, "stable_facts"),
        "preferences": _profile_list(profile, "preferences"),
        "communication_style": str(profile.get("communication_style", "")),
        "recent_focus": _profile_list(profile, "recent_focus"),
        "boundaries": _profile_list(profile, "boundaries"),
        "confidence": dict(profile.get("confidence", {})) if isinstance(profile.get("confidence", {}), dict) else {},
        "evidence_message_ids": _profile_list(profile, "evidence_message_ids"),
        "needs_review": _profile_list(profile, "needs_review"),
    }
    for key, value in profile.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def _normalize_group_profile(profile: dict[str, object]) -> dict[str, object]:
    """规范化群画像格式。

    Args:
        profile: 原始画像字典

    Returns:
        规范化后的画像字典，包含标准字段
    """
    bot_policy = profile.get("bot_policy", {})
    bot_policy = bot_policy if isinstance(bot_policy, dict) else {}
    normalized = {
        "group_style": str(profile.get("group_style", "")),
        "common_topics": _profile_list(profile, "common_topics"),
        "active_speakers": dict(profile.get("active_speakers", {})) if isinstance(profile.get("active_speakers", {}), dict) else {},
        "bot_policy": {
            "reply_when_mentioned": bool(bot_policy.get("reply_when_mentioned", True)),
            "avoid_random_interruption": bool(bot_policy.get("avoid_random_interruption", True)),
        },
        "noise_level": str(profile.get("noise_level", "quiet")),
        "evidence_message_ids": _profile_list(profile, "evidence_message_ids"),
    }
    for key, value in profile.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def _extract_preference_object(text: str) -> str:
    """从偏好表达中提取偏好对象。

    Args:
        text: 消息文本内容

    Returns:
        偏好对象字符串，最大长度 40
    """
    for token in ["不喜欢", "喜欢", "讨厌", "更喜欢", "偏好", "爱吃", "爱用"]:
        if token in text:
            tail = text.split(token, 1)[1].strip()
            tail = tail.lstrip("：:，,。！？ ")
            if tail.endswith("了"):
                tail = tail[:-1]
            return tail[:40]
    return ""


def _apply_user_profile_signal(profile: dict[str, object], text: str, message_id: str) -> tuple[dict[str, object], bool]:
    """根据文本信号更新用户画像。

    Args:
        profile: 当前用户画像
        text: 消息文本内容
        message_id: 消息 ID

    Returns:
        元组（更新后的画像，是否发生变化）
    """
    profile = _normalize_user_profile(profile)
    kind = _profile_signal_kind(text)
    if not kind:
        return profile, False
    snippet = _safe_tail(text)
    changed = False
    evidence = _profile_list(profile, "evidence_message_ids")
    if message_id and message_id not in evidence:
        evidence.append(message_id)
        profile["evidence_message_ids"] = evidence[-30:]
        changed = True
    if kind == "remember":
        focus = _profile_list(profile, "recent_focus")
        focus.append(snippet)
        profile["recent_focus"] = focus[-5:]
        changed = True
    elif kind == "preference":
        prefs = _profile_list(profile, "preferences")
        token = _extract_preference_object(text)
        if token and ("不喜欢" in text or "讨厌" in text):
            prefs = [item for item in prefs if token not in str(item)]
            review = _profile_list(profile, "needs_review")
            review.append(f"pref-conflict:{token}")
            profile["needs_review"] = review[-10:]
            snippet = f"不喜欢 {token}"
        prefs.append(snippet)
        profile["preferences"] = prefs[-10:]
        changed = True
    elif kind == "project":
        focus = _profile_list(profile, "recent_focus")
        focus.append(snippet)
        profile["recent_focus"] = focus[-5:]
        changed = True
    elif kind == "boundary":
        boundaries = _profile_list(profile, "boundaries")
        boundaries.append(snippet)
        profile["boundaries"] = boundaries[-10:]
        changed = True
    elif kind == "fact":
        facts = _profile_list(profile, "stable_facts")
        facts.append(snippet)
        profile["stable_facts"] = facts[-10:]
        changed = True
    if "我不喜欢" in text or "不再" in text:
        token = _extract_preference_object(text)
        if token:
            review = _profile_list(profile, "needs_review")
            review.append(f"conflict:{token}")
            profile["needs_review"] = review[-10:]
    return profile, changed


def _apply_group_profile_signal(
    profile: dict[str, object],
    text: str,
    user_id: str,
    message_id: str,
    cooldown_state: dict[str, object] | None,
) -> tuple[dict[str, object], bool]:
    """根据文本信号更新群画像。

    Args:
        profile: 当前群画像
        text: 消息文本内容
        user_id: 用户 ID
        message_id: 消息 ID
        cooldown_state: 冷却状态字典

    Returns:
        元组（更新后的画像，是否发生变化）
    """
    profile = _normalize_group_profile(profile)
    heat_state = str((cooldown_state or {}).get("heat_state", "quiet"))
    if heat_state in {"hot", "flood"}:
        return profile, False
    snippet = _safe_tail(text, 80)
    changed = False
    if not _looks_like_question(text):
        topics = _profile_list(profile, "common_topics")
        topics.append(snippet)
        profile["common_topics"] = topics[-10:]
        changed = True
    active = profile.get("active_speakers", {})
    if not isinstance(active, dict):
        active = {}
    active[user_id] = int(active.get(user_id, 0)) + 1
    profile["active_speakers"] = active
    profile["noise_level"] = heat_state
    if message_id:
        evidence = _profile_list(profile, "evidence_message_ids")
        if message_id not in evidence:
            evidence.append(message_id)
            profile["evidence_message_ids"] = evidence[-50:]
            changed = True
    return profile, changed or bool(active)


def _render_profile_md(profile: dict[str, object], title: str) -> str:
    """将画像渲染为 Markdown 格式。

    Args:
        profile: 画像字典
        title: 标题文本

    Returns:
        Markdown 格式的画像文本
    """
    if not profile:
        return ""
    lines = [f"### {title}"]
    for key, value in profile.items():
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
        elif isinstance(value, dict):
            rendered = ", ".join(f"{k}={v}" for k, v in value.items())
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)
