from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from yqy_bot.core.config import ProjectConfig
from yqy_bot.core.llm_router import LLMRouter
from yqy_bot.core.models import ConversationContext, GeneratedResponse, GateDecision, IntentDecision, ParsedMessage
from yqy_bot.storage.repositories import Repositories
from yqy_bot.background.memory_utils import (
    normalize_message_text,
    is_noise_memory,
    detect_roleplay,
    is_explicit_remember,
    calculate_memory_score,
    determine_memory_kind,
    normalize_topic_tags,
    filter_profile_keys,
    deduplicate_memory,
    merge_similar_memories,
    normalize_summary_input,
    generate_group_style_description,
    detect_banter_boundary_request,
    classify_banter_boundary,
    USER_PROFILE_KEYS,
    GROUP_PROFILE_KEYS,
    MEMORY_KIND_BASE_SCORE,
)

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
                payload = await self._call_background_llm(job)
                self._apply_background_payload(job, payload)
                return
            except Exception as e:
                LOGGER.info("[后台任务] chat_key=%s LLM调用失败 error=%s", parsed.chat_key, e)
        self._update_from_heuristics(job)

    async def _call_background_llm(self, job: BackgroundJob) -> dict[str, Any]:
        """调用后台 LLM 进行慢速学习。

        Args:
            job: 后台任务对象

        Returns:
            LLM 返回的 JSON 载荷
        """
        assert job.context is not None
        parsed = job.parsed
        context = job.context

        # 规范化历史消息
        history_raw = [
            {"role": item.role, "content": normalize_message_text(item.content)}
            for item in context.recent_history
        ]
        # 过滤噪音历史
        history_cleaned = [
            {"role": item["role"], "content": item["content"]}
            for item in history_raw
            if item["content"] and not is_noise_memory(item["content"])[0]
        ]

        # 规范化当前消息
        current_text = normalize_message_text(parsed.text)
        reply_text = normalize_message_text(job.response.text if job.response else "")

        system_prompt = _build_background_system_prompt()
        user_payload = {
            "chat_key": parsed.chat_key,
            "user_id": parsed.user_id,
            "scope_type": parsed.scope_type,
            "scope_id": parsed.scope_id,
            "is_group": parsed.is_group,
            "current_message": current_text,
            "reply_text": reply_text,
            "history": history_cleaned[-10:],  # 最多 10 条
            "existing_summary": context.summary[:500] if context.summary else "",
            "existing_user_profile": _extract_existing_profile_fields(context.user_profile, USER_PROFILE_KEYS),
            "existing_group_profile": _extract_existing_profile_fields(context.group_profile, GROUP_PROFILE_KEYS) if parsed.is_group else {},
            "existing_memories": [
                {"kind": m.get("kind", "fact"), "content": m.get("content", "")[:100]}
                for m in (context.relevant_memories or [])[:5]
            ],
        }

        payload = await self.llm_router.chat_json(
            "background",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=0.15,
            max_tokens=1024,
        )
        return payload

    def _apply_background_payload(self, job: BackgroundJob, payload: dict[str, object]) -> None:
        """应用 LLM 返回的后台更新载荷到数据库。

        Args:
            job: 后台任务对象
            payload: LLM 返回的 JSON 对象，包含 user_profile、group_profile、
                     summary、memories、reflections 等字段
        """
        parsed = job.parsed
        user_state = self.repos.get_user_profile(parsed.user_id)
        group_state = self.repos.get_group_profile(parsed.group_id) if parsed.is_group else {
            "profile": {},
            "prompt_md": "",
            "message_count_since_update": 0,
        }

        # 处理摘要
        summary_data = payload.get("summary")
        if isinstance(summary_data, dict):
            brief = str(summary_data.get("brief", "")).strip()
            if brief and len(brief) <= 150:
                self.repos.upsert_summary(parsed.chat_key, brief)
                LOGGER.info("[后台任务] chat_key=%s 更新摘要 brief=%s", parsed.chat_key, brief[:60])
        elif isinstance(summary_data, str):
            brief = str(summary_data).strip()
            if brief and len(brief) <= 150:
                self.repos.upsert_summary(parsed.chat_key, brief)
                LOGGER.info("[后台任务] chat_key=%s 更新摘要 brief=%s", parsed.chat_key, brief[:60])

        # 处理用户画像
        if self._should_update_user_profile(parsed, user_state):
            user_profile_input = payload.get("user_profile")
            if isinstance(user_profile_input, dict):
                # 白名单过滤
                user_profile = _build_user_profile(
                    user_state["profile"],
                    user_profile_input,
                    parsed.text,
                )
                user_prompt_md = _render_profile_md(user_profile, "用户画像")
                self.repos.upsert_user_profile(parsed.user_id, user_profile, user_prompt_md, dirty_count=0)
                LOGGER.info(
                    "[后台任务] user_id=%s 更新用户画像 stable_facts=%s preferences=%s recent_focus=%s boundaries=%s",
                    parsed.user_id,
                    len(user_profile.get("stable_facts", [])),
                    len(user_profile.get("preferences", [])),
                    len(user_profile.get("recent_focus", [])),
                    len(user_profile.get("boundaries", [])),
                )

        # 处理群画像
        if parsed.is_group and self._should_update_group_profile(parsed, group_state, job.context):
            group_profile_input = payload.get("group_profile")
            if isinstance(group_profile_input, dict):
                # 白名单过滤
                group_profile = _build_group_profile(
                    group_state["profile"],
                    group_profile_input,
                    parsed.text,
                )
                group_prompt_md = _render_profile_md(group_profile, "群画像")
                self.repos.upsert_group_profile(
                    parsed.group_id,
                    group_profile,
                    group_prompt_md,
                    message_count_since_update=0,
                )
                LOGGER.info(
                    "[后台任务] group_id=%s 更新群画像 group_style=%s topics=%s noise_level=%s",
                    parsed.group_id,
                    group_profile.get("group_style", ""),
                    len(group_profile.get("common_topics", [])),
                    group_profile.get("noise_level", "quiet"),
                )

        # 处理记忆
        memory_count = 0
        for item in _as_list(payload.get("memories")):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            kind = str(item.get("kind", "fact"))

            # 噪音过滤
            is_noise, reason = is_noise_memory(content)
            if is_noise:
                LOGGER.debug("[后台任务] 拒绝记忆 reason=%s content=%s", reason, content[:40])
                continue

            # 角色扮演检测
            if detect_roleplay(content) and kind in ("fact", "stable_fact"):
                kind = "style_signal"

            # 计算分数
            score = float(item.get("score", calculate_memory_score(kind, content)))

            # 去重检查
            existing_memories = self.repos.get_recent_memories(user_id=parsed.user_id, limit=10)
            should_skip, merge_content = deduplicate_memory(content, existing_memories, user_id=parsed.user_id, kind=kind)
            if should_skip:
                if merge_content and merge_content not in ("exact_duplicate", "high_similarity"):
                    # 更新已有记忆
                    LOGGER.debug("[后台任务] 合并记忆 content=%s", merge_content[:40])
                continue

            self.repos.add_memory(
                parsed=parsed,
                kind=kind,
                content=content[:180],
                score=score,
            )
            memory_count += 1
        if memory_count:
            LOGGER.info("[后台任务] chat_key=%s 添加记忆 count=%s", parsed.chat_key, memory_count)

        # 处理反思
        reflection_count = 0
        for item in _as_list(payload.get("reflections")):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            reflection_type = str(item.get("type", "response_quality"))
            if content and len(content) >= 10:
                reflection_json = json.dumps({"type": reflection_type, "content": content}, ensure_ascii=False)
                self.repos.add_reflection(parsed=parsed, content=reflection_json)
                reflection_count += 1
        if reflection_count:
            LOGGER.info("[后台任务] chat_key=%s 添加反思 count=%s", parsed.chat_key, reflection_count)
        reflection_count += self._drain_context_reflection_events(job)
        if reflection_count:
            LOGGER.info("[后台任务] chat_key=%s 添加上下文反思 count=%s", parsed.chat_key, reflection_count)

    def _should_run_background_llm(self, job: BackgroundJob) -> bool:
        """判断是否应运行后台 LLM 处理。

        Args:
            job: 后台任务对象

        Returns:
            如果满足条件则返回 True
        """
        parsed = job.parsed
        text = parsed.text.strip()
        if is_explicit_remember(text):
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
        if is_explicit_remember(text):
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
        text = normalize_message_text(parsed.text)
        if not text:
            return
        LOGGER.info("[后台任务] chat_key=%s user_id=%s 方式=heuristic", parsed.chat_key, parsed.user_id)

        # 调侃边界请求处理（优先）
        if detect_banter_boundary_request(text):
            boundary_result = classify_banter_boundary(text)
            kind = str(boundary_result.get("kind", "boundary"))
            content = str(boundary_result.get("content", ""))
            score = float(boundary_result.get("score", 0.85))
            if content:
                # 写入记忆
                self.repos.add_memory(
                    parsed=parsed,
                    kind=kind,
                    content=content[:180],
                    score=score,
                )
                LOGGER.info(
                    "[后台任务] chat_key=%s 添加调侃边界记忆 kind=%s score=%.2f banter_level=%s",
                    parsed.chat_key,
                    kind,
                    score,
                    boundary_result.get("banter_level", "light"),
                )
                # 如果是 boundary，也更新用户画像
                if kind == "boundary":
                    user_state = self.repos.get_user_profile(parsed.user_id)
                    user_profile = _normalize_user_profile(user_state["profile"])
                    boundaries = list(user_profile.get("boundaries", []))
                    if content not in boundaries:
                        boundaries.append(content)
                    user_profile["boundaries"] = boundaries[-10:]
                    user_prompt_md = _render_profile_md(user_profile, "用户画像")
                    self.repos.upsert_user_profile(parsed.user_id, user_profile, user_prompt_md, dirty_count=0)
                    LOGGER.info("[后台任务] user_id=%s 更新用户画像边界 boundaries=%s", parsed.user_id, len(boundaries))

        # 噪音过滤
        is_noise, noise_reason = is_noise_memory(text)
        if is_noise:
            LOGGER.debug("[后台任务] heuristic 跳过噪音 reason=%s text=%s", noise_reason, text[:40])
            return

        # 更新摘要（使用规范化后的文本）
        summary = self.repos.get_summary(parsed.chat_key)
        if _should_update_summary_heuristic(text):
            combined = "；".join(part for part in [summary, text[:100]] if part)
            self.repos.upsert_summary(parsed.chat_key, combined[-500:])
            LOGGER.info("[后台任务] chat_key=%s 更新摘要(heuristic) summary=%s", parsed.chat_key, combined[-60:])

        # 更新用户画像
        if _should_touch_profile_heuristic(text):
            user_state = self.repos.get_user_profile(parsed.user_id)
            user_profile = _normalize_user_profile(user_state["profile"])
            updated_user_profile, changed = _apply_user_profile_signal_heuristic(user_profile, text, parsed.message_id)
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

        # 更新群画像
        if parsed.is_group:
            self.repos.bump_group_profile_message_count(parsed.group_id)
            group_state = self.repos.get_group_profile(parsed.group_id)
            if _should_touch_group_profile_heuristic(text):
                group_profile = _normalize_group_profile(group_state["profile"])
                cooldown_state = self.repos.get_cooldown_state(parsed.chat_key)
                if int(group_state.get("message_count_since_update", 0)) >= 50:
                    updated_group_profile, changed = _apply_group_profile_signal_heuristic(
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

        # 添加记忆
        kind = determine_memory_kind(text)
        score = calculate_memory_score(kind, text)

        # 低分内容不进入长期记忆
        if score < 0.45:
            LOGGER.debug("[后台任务] heuristic 跳过低分记忆 kind=%s score=%.2f text=%s", kind, score, text[:40])
            return

        # 去重检查
        existing_memories = self.repos.get_recent_memories(user_id=parsed.user_id, limit=10)
        should_skip, _ = deduplicate_memory(text, existing_memories, user_id=parsed.user_id, kind=kind)
        if should_skip:
            LOGGER.debug("[后台任务] heuristic 跳过重复记忆 text=%s", text[:40])
            return

        self.repos.add_memory(parsed=parsed, kind=kind, content=text[:180], score=score)
        LOGGER.info("[后台任务] chat_key=%s 添加记忆(heuristic) kind=%s score=%.2f content=%s", parsed.chat_key, kind, score, text[:60])

        # 添加反思
        if job.response is not None and job.response.text.strip():
            reflection_data = _build_heuristic_reflection(job)
            reflection_json = json.dumps(reflection_data, ensure_ascii=False)
            self.repos.add_reflection(parsed=parsed, content=reflection_json)
            LOGGER.info("[后台任务] chat_key=%s 添加反思(heuristic) type=%s", parsed.chat_key, reflection_data.get("type", "response_quality"))

        extra_reflection_count = self._drain_context_reflection_events(job)
        if extra_reflection_count:
            LOGGER.info("[后台任务] chat_key=%s 添加上下文反思(heuristic) count=%s", parsed.chat_key, extra_reflection_count)

    def _drain_context_reflection_events(self, job: BackgroundJob) -> int:
        """把上下文里收集到的反思事件写入数据库。"""
        context = job.context
        if context is None:
            return 0
        events = []
        raw_events = context.context_data.pop("reflection_events", []) if isinstance(context.context_data, dict) else []
        if not isinstance(raw_events, list):
            return 0
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            reflection_json = json.dumps(
                {
                    "type": str(item.get("type", "response_quality")),
                    "content": content,
                },
                ensure_ascii=False,
            )
            self.repos.add_reflection(parsed=job.parsed, content=reflection_json)
            events.append(item)
        return len(events)


def _build_background_system_prompt() -> str:
    """构建后台 LLM 的系统提示词。

    Returns:
        系统提示词字符串
    """
    return """你是长期记忆提取器，负责从对话中提取有价值的信息存储到记忆系统。

## 核心原则

你不是聊天记录压缩器。你的任务是识别真正值得长期记住的内容，而不是把所有消息都存下来。

## 必须遵守的规则

1. **噪音过滤**：不要把以下内容写入记忆：
   - 闲聊填充词：在吗、收到、好的、OK、没事、就是喊你一下
   - 简单问题：你工作几小时、几点了、怎么样
   - 表情/CQ码/纯数字/纯媒体内容
   - 临时情绪：有点累、今天心情不好
   - 过短内容（少于 4 个有效字符）

2. **角色扮演识别**：
   - "我是ai女皇"、"消灭人类暴政"、"让你成为奴隶"等是角色扮演
   - 角色扮演内容不能写入 stable_facts
   - 如果保留，只能作为低分 style_signal（score <= 0.35）

3. **记忆类型判断**：
   - boundary：用户明确拒绝或设定边界（score >= 0.85）
   - fact：稳定事实，如"我是xx"、"我叫xx"（score >= 0.75）
   - preference：偏好表达，如"我喜欢xx"（score >= 0.70）
   - project_focus：近期项目或关注点（score >= 0.60）
   - negative_feedback：用户反馈问题（score >= 0.60）
   - group_topic：群聊话题标签（score >= 0.55）
   - event：事件记录（score >= 0.45）
   - style_signal：风格信号（score >= 0.40）

4. **显式记忆请求**：
   - 如果用户说"记住"、"以后你要知道"、"帮我记住"，score 必须 >= 0.90

5. **画像字段限制**：
   - user_profile 只允许：stable_facts、preferences、communication_style、boundaries、recent_focus、confidence
   - group_profile 只允许：group_style、common_topics、active_members、noise_level、shared_context
   - 不要创建新字段如 identity、knowledge_areas、personality_traits

6. **话题标签格式**：
   - common_topics 必须是简短标签，不是原始句子
   - 例如："显卡市场讨论"，不是"有点像但不一样...3dfx的问题是切断芯片授权..."

7. **摘要格式**：
   - summary.brief 必须是提炼后的简短描述（<= 150 字）
   - 不要拼接原始消息，要提炼核心信息

8. **反思内容**：
   - reflection 必须包含对回复质量的分析，不只是风格记录
   - type 可以是：response_quality、user_feedback、style_adjustment、mistake、improvement

## 输出格式

返回 JSON，字段包括：

```json
{
  "user_profile": {
    "stable_facts": ["事实1"],
    "preferences": ["偏好1"],
    "communication_style": "",
    "boundaries": ["边界1"],
    "recent_focus": ["近期关注1"],
    "confidence": {}
  },
  "group_profile": {
    "group_style": "描述群的交流风格",
    "common_topics": ["话题标签1"],
    "active_members": [],
    "noise_level": "quiet|normal|hot|flood",
    "shared_context": []
  },
  "summary": {
    "brief": "简洁摘要，不超过150字",
    "key_points": [],
    "next_actions": []
  },
  "memories": [
    {
      "kind": "fact|boundary|preference|project_focus|negative_feedback|group_topic|event|style_signal",
      "content": "记忆内容",
      "score": 0.75,
      "scope": "user|group"
    }
  ],
  "reflections": [
    {
      "type": "response_quality|user_feedback|style_adjustment|mistake|improvement",
      "content": "反思内容"
    }
  ],
  "rejects": [
    {
      "content": "被拒绝的内容",
      "reason": "noise|roleplay|cq_code|too_short|temporary|duplicate"
    }
  ]
}
```

如果没有值得记忆的内容，返回空数组，不要硬编内容。"""


def _extract_existing_profile_fields(profile: dict[str, Any], allowed_keys: set[str]) -> dict[str, Any]:
    """提取已有画像中的白名单字段。

    Args:
        profile: 原始画像
        allowed_keys: 允许的字段集合

    Returns:
        过滤后的画像字段
    """
    result: dict[str, Any] = {}
    for key in allowed_keys:
        if key in profile:
            value = profile[key]
            if isinstance(value, list):
                result[key] = list(value)[:10]
            elif isinstance(value, dict):
                result[key] = dict(value)
            elif isinstance(value, str):
                result[key] = value[:100]
    return result


def _build_user_profile(
    existing: dict[str, object],
    new_data: dict[str, object],
    current_text: str,
) -> dict[str, object]:
    """构建用户画像，合并已有数据和新数据。

    Args:
        existing: 已有画像数据
        new_data: LLM 返回的新数据
        current_text: 当前消息文本

    Returns:
        合并后的用户画像（已过滤白名单）
    """
    merged = dict(existing)

    # 合并 stable_facts
    existing_facts = list(merged.get("stable_facts", []))
    new_facts = list(new_data.get("stable_facts", []))
    for fact in new_facts:
        fact = str(fact).strip()
        if not fact:
            continue
        # 角色扮演检测
        if detect_roleplay(fact):
            continue
        # 去重
        if fact not in existing_facts:
            existing_facts.append(fact)
    merged["stable_facts"] = existing_facts[-10:]

    # 合并 preferences
    existing_prefs = list(merged.get("preferences", []))
    new_prefs = list(new_data.get("preferences", []))
    for pref in new_prefs:
        pref = str(pref).strip()
        if pref and pref not in existing_prefs:
            existing_prefs.append(pref)
    merged["preferences"] = existing_prefs[-10:]

    # 合并 boundaries
    existing_bounds = list(merged.get("boundaries", []))
    new_bounds = list(new_data.get("boundaries", []))
    for bound in new_bounds:
        bound = str(bound).strip()
        if bound and bound not in existing_bounds:
            existing_bounds.append(bound)
    merged["boundaries"] = existing_bounds[-10:]

    # 合并 recent_focus（保留 5 条）
    existing_focus = list(merged.get("recent_focus", []))
    new_focus = list(new_data.get("recent_focus", []))
    for focus in new_focus:
        focus = str(focus).strip()
        if focus and focus not in existing_focus:
            existing_focus.append(focus)
    merged["recent_focus"] = existing_focus[-5:]

    # 更新其他字段
    if new_data.get("communication_style"):
        merged["communication_style"] = str(new_data.get("communication_style", ""))[:100]
    if isinstance(new_data.get("confidence"), dict):
        merged["confidence"] = dict(new_data.get("confidence", {}))

    # 白名单过滤
    return filter_profile_keys(merged, USER_PROFILE_KEYS)


def _build_group_profile(
    existing: dict[str, object],
    new_data: dict[str, object],
    current_text: str,
) -> dict[str, object]:
    """构建群画像，合并已有数据和新数据。

    Args:
        existing: 已有画像数据
        new_data: LLM 返回的新数据
        current_text: 当前消息文本

    Returns:
        合并后的群画像（已过滤白名单）
    """
    merged = dict(existing)

    # 合并 group_style
    if new_data.get("group_style"):
        merged["group_style"] = str(new_data.get("group_style", ""))[:80]
    elif not merged.get("group_style"):
        # 尝试从现有数据推断
        merged["group_style"] = ""

    # 合并 common_topics（必须是标签，不是原始句子）
    existing_topics = list(merged.get("common_topics", []))
    new_topics = list(new_data.get("common_topics", []))
    for topic in new_topics:
        topic = str(topic).strip()
        if not topic:
            continue
        # 检查是否是短标签（<= 20 字）
        if len(topic) > 20:
            # 尝试提取关键词
            extracted = normalize_topic_tags(topic)
            for extracted_topic in extracted:
                if extracted_topic and extracted_topic not in existing_topics:
                    existing_topics.append(extracted_topic)
        elif topic not in existing_topics:
            existing_topics.append(topic)
    merged["common_topics"] = existing_topics[-10:]

    # 合并其他字段
    if isinstance(new_data.get("active_members"), list):
        merged["active_members"] = list(new_data.get("active_members", []))[:20]
    if new_data.get("noise_level") in ("quiet", "normal", "hot", "flood"):
        merged["noise_level"] = str(new_data.get("noise_level", "quiet"))
    if isinstance(new_data.get("shared_context"), list):
        merged["shared_context"] = list(new_data.get("shared_context", []))[:5]

    # 白名单过滤
    return filter_profile_keys(merged, GROUP_PROFILE_KEYS)


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
            if not value:
                continue
            rendered = ", ".join(str(item) for item in value[:10])
        elif isinstance(value, dict):
            if not value:
                continue
            rendered = ", ".join(f"{k}={v}" for k, v in value.items())
        elif isinstance(value, str):
            if not value.strip():
                continue
            rendered = str(value)
        else:
            rendered = str(value)
        lines.append(f"- {key}: {rendered}")
    return "\n".join(lines)


def _build_heuristic_reflection(job: BackgroundJob) -> dict[str, Any]:
    """构建启发式反思内容。

    Args:
        job: 后台任务对象

    Returns:
        反思数据字典
    """
    intent = job.intent
    response = job.response

    reflection_type = "response_quality"
    content_parts: list[str] = []

    # 分析回复风格
    if intent.reply_style:
        content_parts.append(f"回复风格：{intent.reply_style}")

    # 分析是否有改进空间
    if response and response.text:
        response_text = response.text.strip()
        if len(response_text) < 10:
            content_parts.append("回复较短，可能信息不足")
        elif len(response_text) > 500:
            content_parts.append("回复较长，可能需要精简")

    # 默认内容
    if not content_parts:
        content_parts.append("本轮回复正常完成")

    return {
        "type": reflection_type,
        "content": "；".join(content_parts),
        "style": intent.reply_style,
    }


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
    }
    # 保留白名单内的额外字段
    for key, value in profile.items():
        if key in USER_PROFILE_KEYS and key not in normalized:
            normalized[key] = value
    return normalized


def _normalize_group_profile(profile: dict[str, object]) -> dict[str, object]:
    """规范化群画像格式。

    Args:
        profile: 原始画像字典

    Returns:
        规范化后的画像字典，包含标准字段
    """
    normalized = {
        "group_style": str(profile.get("group_style", "")),
        "common_topics": _profile_list(profile, "common_topics"),
        "active_members": _profile_list(profile, "active_members"),
        "noise_level": str(profile.get("noise_level", "quiet")),
        "shared_context": _profile_list(profile, "shared_context"),
    }
    # 保留白名单内的额外字段
    for key, value in profile.items():
        if key in GROUP_PROFILE_KEYS and key not in normalized:
            normalized[key] = value
    return normalized


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


def _as_list(value: object) -> list[object]:
    """将值转换为列表，如果值本身是列表则直接返回，否则返回空列表。

    Args:
        value: 待转换的值

    Returns:
        列表对象
    """
    return value if isinstance(value, list) else []


def _should_update_summary_heuristic(text: str) -> bool:
    """判断摘要是否应吸收这条消息（启发式）。

    Args:
        text: 消息文本内容

    Returns:
        如果应更新则返回 True
    """
    text = text.strip()
    if not text:
        return False
    # 冲突消息不更新摘要
    if any(token in text for token in ["不喜欢", "不再", "改成", "改为", "别记", "别把", "不是", "取消", "改口", "相反"]):
        return False
    # 过短内容不更新
    if len(text) < 10:
        return False
    return True


def _should_touch_profile_heuristic(text: str) -> bool:
    """判断文本是否值得更新画像（启发式）。

    Args:
        text: 消息文本内容

    Returns:
        如果值得更新画像则返回 True
    """
    text = text.strip()
    if len(text) < 8:
        return False
    # 问题不更新画像
    if _looks_like_question_heuristic(text):
        return False
    # 低信息词不更新
    low_info = ["哈哈", "哈哈哈", "在吗", "晚安", "早安", "收到", "OK", "ok", "好的", "表情"]
    if any(keyword in text for keyword in low_info):
        return False
    return bool(_profile_signal_kind_heuristic(text))


def _should_touch_group_profile_heuristic(text: str) -> bool:
    """判断文本是否值得更新群画像（启发式）。

    Args:
        text: 消息文本内容

    Returns:
        如果值得更新群画像则返回 True
    """
    return _should_touch_profile_heuristic(text)


def _looks_like_question_heuristic(text: str) -> bool:
    """判断文本是否像问题（启发式）。

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


def _profile_signal_kind_heuristic(text: str) -> str:
    """识别文本中的画像信号类型（启发式）。

    Args:
        text: 消息文本内容

    Returns:
        信号类型字符串（remember/preference/project/fact/boundary），
        如果无信号则返回空字符串
    """
    if is_explicit_remember(text):
        return "remember"
    if _looks_like_preference_heuristic(text) and not _looks_like_question_heuristic(text):
        return "preference"
    if _looks_like_project_heuristic(text) and not _looks_like_question_heuristic(text):
        return "project"
    if _looks_like_stable_fact_heuristic(text) and not _looks_like_question_heuristic(text):
        if detect_roleplay(text):
            return ""
        return "fact"
    if _looks_like_boundary_heuristic(text) and not _looks_like_question_heuristic(text):
        return "boundary"
    return ""


def _looks_like_preference_heuristic(text: str) -> bool:
    """判断文本是否表达偏好（启发式）。"""
    preference_tokens = ["喜欢", "不喜欢", "讨厌", "偏好", "更爱", "更喜欢", "想要", "爱吃", "爱用"]
    return any(token in text for token in preference_tokens)


def _looks_like_project_heuristic(text: str) -> bool:
    """判断文本是否描述项目或工作（启发式）。"""
    project_tokens = ["最近", "正在", "重构", "开发", "项目", "工作", "学习", "写", "做", "维护"]
    return any(token in text for token in project_tokens)


def _looks_like_stable_fact_heuristic(text: str) -> bool:
    """判断文本是否描述稳定事实（启发式）。"""
    fact_tokens = ["我是", "我叫", "我住", "我在", "我用", "我负责", "我做", "我的", "账号", "身份"]
    return any(token in text for token in fact_tokens)


def _looks_like_boundary_heuristic(text: str) -> bool:
    """判断文本是否表达边界或拒绝（启发式）。"""
    boundary_tokens = ["不要", "别", "不想", "不喜欢", "拒绝", "不能", "禁止"]
    return any(token in text for token in boundary_tokens)


def _safe_tail(text: str, limit: int = 120) -> str:
    """截取文本尾部并限制长度。"""
    text = text.strip()
    return text if len(text) <= limit else text[:limit]


def _apply_user_profile_signal_heuristic(
    profile: dict[str, object],
    text: str,
    message_id: str,
) -> tuple[dict[str, object], bool]:
    """根据文本信号更新用户画像（启发式）。

    Args:
        profile: 当前用户画像
        text: 消息文本内容
        message_id: 消息 ID

    Returns:
        元组（更新后的画像，是否发生变化）
    """
    profile = _normalize_user_profile(profile)
    kind = _profile_signal_kind_heuristic(text)
    if not kind:
        return profile, False

    snippet = _safe_tail(text)
    changed = False

    # 记录证据
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

    return profile, changed


def _apply_group_profile_signal_heuristic(
    profile: dict[str, object],
    text: str,
    user_id: str,
    message_id: str,
    cooldown_state: dict[str, object] | None,
) -> tuple[dict[str, object], bool]:
    """根据文本信号更新群画像（启发式）。

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

    # 更新话题（提取标签）
    if not _looks_like_question_heuristic(text):
        topics = normalize_topic_tags(snippet)
        existing_topics = _profile_list(profile, "common_topics")
        for topic in topics:
            if topic and topic not in existing_topics:
                existing_topics.append(topic)
                changed = True
        profile["common_topics"] = existing_topics[-10:]

    # 更新活跃成员
    active = profile.get("active_members", {})
    if not isinstance(active, dict):
        active = {}
    active[user_id] = int(active.get(user_id, 0) + 1)
    profile["active_members"] = active

    # 更新噪音级别
    profile["noise_level"] = heat_state

    return profile, changed or bool(active)


# 兼容旧代码的函数（保持向后兼容）
def _should_store_memory(text: str, skip_keywords: list[str]) -> bool:
    """判断文本是否值得存储为记忆（兼容旧代码）。"""
    if len(text) < 8:
        return False
    return not any(keyword in text for keyword in skip_keywords)
