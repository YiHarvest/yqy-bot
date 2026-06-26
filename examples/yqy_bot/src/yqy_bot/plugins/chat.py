from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from iamai import Context, Event, LLMClient, LLMConfig, Plugin, message_handler, middleware
from iamai.config import load_env_file
from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_PROJECT_ROOT))

load_env_file(_PROJECT_ROOT / ".env")

_CONFIG_DIR = _PROJECT_ROOT / "config"


def _load_json(filename: str) -> dict:
    path = _CONFIG_DIR / filename
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


_bot = _load_json("bot.json")
_prompt = _load_json("prompt.json")
_users_cfg = _load_json("users.json")
_reflection_cfg = _load_json("reflection.json")
_fact_guard = _load_json("fact_guard.json")

MAX_HISTORY_TURNS: int = int(_bot.get("max_history_turns", 15))
USERS: dict[str, dict[str, str]] = _users_cfg
MESSAGES_BEFORE_REFLECTION: int = int(_reflection_cfg.get("messages_before_reflection", 20))

# ── 结构化 prompt 字段 ──
_IDENTITY: str = _prompt.get("identity", "")
_PERSONALITY: list[str] = _prompt.get("personality", [])
_SCENARIO: str = _prompt.get("scenario", "")
_SPEECH_STYLE: list[str] = _prompt.get("speech_style", [])
_FACT_BOUNDARY: list[str] = _prompt.get("fact_boundary", [])
_MEMORY_RULES: list[str] = _prompt.get("memory_rules", [])
_REFLECTION_RULES: list[str] = _prompt.get("reflection_rules", [])
_EXAMPLES: dict[str, Any] = _prompt.get("examples", {})
_OUTPUT_SCHEMA: list[str] = _prompt.get("output_schema", [])

# ── 事实闸门配置 ──
_HIGH_RISK_TRIGGERS: list[str] = _fact_guard.get("high_risk_triggers", [])
_FALLBACK_REPLY: dict[str, Any] = _fact_guard.get("fallback_reply", {"text": "行，我收敛点。", "face_id": "", "send_meme": False})
_REWRITE_PROMPT: str = _fact_guard.get("rewrite_prompt", "请删除无依据细节，只保留基于当前用户消息的调侃。最多一句话。返回JSON。")

# ── 毒性过滤：代码层硬拦截，prompt 挡不住的内容这里直接杀死 ──
_TOXIC_PATTERNS: list[str] = _fact_guard.get("toxic_patterns", [
    "你爹", "亲爹", "我是你爸", "你爸爸",
    "换个脑子", "换脑子",
    "你清醒点", "清醒点行不",
    "翻个白眼", "翻白眼",
    "这问题问得够傻", "这问题傻",
    "你赢了 我闭嘴 懒得跟你争",
    "这都记不住",
    "你是不是该",
])
_TOXIC_FALLBACK: dict[str, Any] = {"text": "抱歉，我注意语气。", "face_id": "", "send_meme": False}

# ── 表情包配文策略 ──
# "never": 有图不发字  "short": 仅 ≤8 字时保留配文
MEME_CAPTION_MODE: str = "never"

# ── 热重载兼容：仅在 reload 时（非首次导入）清空 services 模块缓存 ──
# 排除 meme_server（它由 run.py 独立启动，不应被插件重载销毁）
_IMPORTED = globals().get("_IMPORTED", False)
if _IMPORTED:
    for _mod in list(sys.modules):
        if _mod.startswith("services.") and _mod != "services.meme_server":
            del sys.modules[_mod]
_IMPORTED = True

from services.db import init_db
from services.history import HistoryService
from services.memory import MemoryService
from services.memory_filter import MIN_TEXT_LENGTH, QUESTION_MARKS, SKIP_KEYWORDS
from services.mood import (  # noqa: E501
    NEGATIVE_KEYWORDS,
    POSITIVE_KEYWORDS,
    MoodService,
    REACTION_LONELINESS_DELTA,
    REACTION_NEGATIVE_MOOD_DELTA,
    REACTION_POSITIVE_MOOD_DELTA,
    REPLY_ENERGY_DELTA,
)
from services.relation import (
    NEGATIVE_FAVORABILITY_DELTA,
    POSITIVE_FAVORABILITY_DELTA,
    RELATION_NEGATIVE,
    RELATION_POSITIVE,
    REPLY_INTIMACY_DELTA,
    RelationshipService,
)
from services.behavior import BehaviorService
from services.human_behavior import send_human_like as _send_human
from services.meme_service import MemeService
from services.persona import PersonaService
from services.reflection import (  # noqa: E501
    MAX_REFLECTIONS_PER_DAY,
    ReflectionService,
)
from services.social_memory import SocialMemoryService
from services.reasoning import RelationshipReasoningService


class YqyChatPlugin(Plugin):
    name = "yqy_chat"
    description = "YQY 的个人聊天机器人。"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        init_db()
        self._history_service = HistoryService()
        self._memory_service = MemoryService()
        self._mood_service = MoodService()
        self._relation_service = RelationshipService()
        self._behavior_service = BehaviorService()
        self._meme_service = MemeService()
        self._persona_service = PersonaService()
        self._reflection_service = ReflectionService()
        self._social_memory_service = SocialMemoryService()
        self._reasoning_service = RelationshipReasoningService()
        self._msg_counter: int = 0
        self._meme_streak: int = 0  # 连续表情包计数
        # ── 收藏表情状态机 ──
        self._pending_meme_save: dict[str, dict[str, str]] = {}  # user_id → {emotion, tags}

    def _get_history(self, session_id: str) -> list[dict[str, str]]:
        return self._history_service.get_recent_history(session_id, MAX_HISTORY_TURNS)

    def _append_history(self, session_id: str, user_msg: str, ai_text: str) -> None:
        self._history_service.append_turn(session_id, user_msg, ai_text)

    @middleware(priority=5, phase="before")
    async def record_source(self, ctx: Context) -> None:
        ctx.shared_state["last_user"] = ctx.event.user_id
        ctx.shared_state["last_channel"] = ctx.event.channel_id

    @message_handler(priority=90)
    async def free_chat(self, ctx: Context, event: Event) -> None:
        # 如果已被收藏表情指令处理，跳过
        if ctx.shared_state.get("meme_handled"):
            return

        # 时间衰减
        self._mood_service.tick()

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

        # 消息内容影响情绪
        self._adjust_mood_from_text(text)

        session_id = event.channel_id or event.user_id or "default"
        user_id = event.user_id or session_id

        # 关系系统：确保用户存在，更新聊天时间，调整关系值
        self._relation_service.get_or_create_user(user_id)
        self._relation_service.update_last_chat_time(user_id)
        self._adjust_relation_from_text(text, user_id)

        result = await self._ai_reply(user_msg, session_id, user_id)
        await self._send_reply(ctx, result, session_id, user_id)

    # ═══════════════════════════════════════════
    #  收藏表情指令（优先级 95，高于 free_chat 的 90）
    # ═══════════════════════════════════════════

    _MEME_SAVE_KEYWORDS: tuple[str, ...] = tuple(_bot.get("meme_save_keywords", []))
    _MEME_SEND_KEYWORDS: tuple[str, ...] = tuple(_bot.get("meme_send_keywords", []))

    @message_handler(priority=95)
    async def handle_meme_commands(self, ctx: Context, event: Event) -> None:
        """处理收藏表情和测试指令。"""
        text = ctx.text.strip()
        user_id = event.user_id or "default"

        # ── 测试指令：随机发一张本地收藏表情 ──
        if any(kw in text for kw in self._MEME_SEND_KEYWORDS):
            ctx.shared_state["meme_handled"] = True
            await self._send_random_favorite(ctx, user_id)
            return

        # ── 「存表情」指令：进入等待模式 ──
        if text in self._MEME_SAVE_KEYWORDS:
            ctx.shared_state["meme_handled"] = True
            self._pending_meme_save[user_id] = {"emotion": "default", "tags": ""}
            await self._send_text_reply(ctx, "把你要我记住的表情发过来，我存。", user_id)
            return

        # ── 「存表情 xxx」带情绪标签 ──
        for kw in ("存表情 ", "收藏表情 ", "学习表情包 "):
            if text.startswith(kw):
                ctx.shared_state["meme_handled"] = True
                emotion = text[len(kw):].strip()
                valid_emotions = {"开心", "阴阳怪气", "安慰", "烦躁", "兴奋", "默认"}
                tag = emotion if emotion in valid_emotions else "default"
                self._pending_meme_save[user_id] = {"emotion": tag, "tags": emotion}
                await self._send_text_reply(ctx, f"好的，下一张图标记为「{emotion}」。", user_id)
                return

        # ── 等待模式：收到图片 → 保存 ──
        if user_id in self._pending_meme_save:
            segments = event.message.segments if hasattr(event.message, "segments") else []
            img_result = self._extract_image_url(segments)
            if img_result:
                ctx.shared_state["meme_handled"] = True
                meta = self._pending_meme_save.pop(user_id)
                img_url, seg_type = img_result
                success = await self._meme_service.save_favorite_meme(
                    user_id=user_id,
                    source=img_url,
                    emotion=meta.get("emotion", "default"),
                    tags=meta.get("tags", ""),
                    is_url=(seg_type == "image"),
                    meme_type=seg_type,
                )
                if success:
                    await self._send_text_reply(ctx, "存好了，下次优先用这个。", user_id)
                else:
                    await self._send_text_reply(ctx, "这个表情我没存上，可能拿不到原图。", user_id)
            else:
                # 没收到图片，取消等待
                self._pending_meme_save.pop(user_id, None)
                ctx.shared_state["meme_handled"] = True
                await self._send_text_reply(ctx, "没看到图片，不存了。", user_id)

    async def _send_random_favorite(self, ctx: Context, user_id: str) -> None:
        """直接发送一张本地收藏表情（不经过 LLM）。"""
        fav = await self._meme_service.get_favorite_meme_url(user_id)
        if fav is None:
            await self._send_text_reply(ctx, "你还没喂过我表情包，先发一张给我存。", user_id)
            return
        adapters = getattr(self.runtime, "adapters", [])
        if adapters:
            from services.human_behavior import _image_segment, _mface_from_dict
            from iamai import Message

            if fav["type"] == "mface":
                segment = _mface_from_dict(fav["data"])
            else:
                segment = _image_segment(fav["url"])
            msg = Message([segment])
            await adapters[0].send_message(msg, target={"user_id": user_id})
            logger.info(f"[测试指令] 发送收藏表情 → {user_id} type={fav['type']}")

    async def _send_text_reply(self, ctx: Context, text: str, user_id: str) -> None:
        """发送纯文本回复（不经过 LLM，不存储历史）。"""
        adapters = getattr(self.runtime, "adapters", [])
        if adapters:
            from iamai import Message
            msg = Message([{"type": "text", "data": {"text": text}}])
            await adapters[0].send_message(msg, target={"user_id": user_id})
            logger.info(f"[指令回复] → {user_id} text={text[:40]}")

    @staticmethod
    def _extract_image_url(segments: list[dict[str, Any]]) -> tuple[str, str] | None:
        """从消息段中提取可下载的图片/表情 URL。

        支持: image, mface, marketface 类型。

        Returns:
            image/marketface:  (url, "image")
            mface:            (json.dumps(data), "mface")  -- mface 无法下载，存段数据
            None:             未找到
        """
        logger.debug(f"[收藏表情] segments={json.dumps(segments, ensure_ascii=False, default=str)[:500]}")
        for seg in segments:
            seg_kind = seg.get("kind", "")
            data = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}

            # ── mface（QQ收藏表情）：无法下载，存整个 data JSON ──
            if seg_kind == "mface":
                logger.info(f"[收藏表情] mface data keys={list(data.keys())}")
                return (json.dumps(data, ensure_ascii=False), "mface")

            if seg_kind in ("image", "marketface"):
                url = data.get("url") or data.get("file") or data.get("path") or ""
                if url:
                    logger.info(f"[收藏表情] 提取URL kind={seg_kind} url={url[:80]}")
                    return (str(url), "image")
        return None

    # ═══════════════════════════════════════════
    #  free_chat 的图片保存逻辑
    # ═══════════════════════════════════════════

    def _build_persona_state(self) -> str:
        """生成星星当前内部状态的文本描述。"""
        return self._mood_service.describe()

    def _build_persona_context(self, user_id: str) -> str:
        """根据当前用户身份和情绪，生成人格状态描述。"""
        rel = self._relation_service.get_or_create_user(user_id)
        mood_state = self._mood_service.get_state()
        return self._persona_service.describe(rel["identity"], mood_state)

    def _adjust_mood_from_text(self, text: str) -> None:
        """根据消息内容中的关键词调整情绪。"""
        if not text:
            return
        self._mood_service.adjust_loneliness(REACTION_LONELINESS_DELTA)
        if any(kw in text for kw in POSITIVE_KEYWORDS):
            self._mood_service.adjust_mood(REACTION_POSITIVE_MOOD_DELTA)
        if any(kw in text for kw in NEGATIVE_KEYWORDS):
            self._mood_service.adjust_mood(REACTION_NEGATIVE_MOOD_DELTA)

    def _adjust_relation_from_text(self, text: str, user_id: str) -> None:
        """根据消息内容调整当前用户的好感度。"""
        if not text:
            return
        if any(kw in text for kw in RELATION_POSITIVE):
            self._relation_service.adjust_favorability(user_id, POSITIVE_FAVORABILITY_DELTA)
        if any(kw in text for kw in RELATION_NEGATIVE):
            self._relation_service.adjust_favorability(user_id, NEGATIVE_FAVORABILITY_DELTA)

    def _build_relationship_context(self, user_id: str) -> str:
        """生成当前聊天对象的上下文描述。"""
        return self._relation_service.describe(user_id)

    def _build_system_prompt(self, user_id: str) -> str:
        """构建酒馆角色卡结构的系统提示词。

        顺序：状态 → 关系 → 人格 → 记忆 → 反思 → 社交 → 约束 → 示例 → 输出格式
        事实边界、说话风格、输出格式放在末尾作为强约束。
        """
        memories = self._memory_service.get_memories(user_id)
        reflections = self._reflection_service.get_recent()
        social_desc = self._social_memory_service.describe_user(user_id)

        parts: list[str] = []

        # ── 0. 当前时间（中国标准时间）──
        now = datetime.now()
        time_str = now.strftime('%Y年%m月%d日 %H:%M')
        weekday = '一二三四五六日'[now.weekday()]
        parts.append(f"【当前北京时间：{time_str} 周{weekday}】以上是真实时间，回答涉及时间时必须以这个时间为准。")
        parts.append("")

        # ── 1. 星星当前状态（动态）──
        parts.append(self._build_persona_state())
        parts.append("")
        parts.append(self._build_persona_context(user_id))

        # ── 2. 关系上下文（动态）──
        parts.append("")
        parts.append(self._build_relationship_context(user_id))

        # ── 3. 人格设定（静态）──
        parts.append("")
        parts.append(_IDENTITY)
        parts.append("")
        parts.append("## 性格")
        for line in _PERSONALITY:
            parts.append(f"- {line}")
        parts.append("")
        parts.append(f"## 场景\n{_SCENARIO}")

        # ── 4. 长期记忆（动态）──
        if memories:
            parts.append("")
            parts.append("## 长期记忆（参考，非绝对事实）")
            for m in memories:
                parts.append(f"- {m}")

        # ── 5. 反思观察（动态）──
        if reflections:
            parts.append("")
            parts.append("## 星星的近期观察（主观判断，非事实）")
            for r in reflections:
                parts.append(f"- {r}")

        # ── 6. 社交记忆（动态）──
        if social_desc:
            parts.append("")
            parts.append("## 人物关系记忆")
            parts.append(social_desc)

        # ── 7. 事实边界（最高优先级约束）──
        parts.append("")
        parts.append("# 事实边界（最高优先级，必须遵守）")
        for line in _FACT_BOUNDARY:
            parts.append(line)

        # ── 8. 记忆使用规则 ──
        parts.append("")
        parts.append("## 记忆使用规则")
        for line in _MEMORY_RULES:
            parts.append(f"- {line}")

        # ── 9. 反思使用规则 ──
        parts.append("")
        parts.append("## 反思使用规则")
        for line in _REFLECTION_RULES:
            parts.append(f"- {line}")

        # ── 10. 说话风格（强约束）──
        parts.append("")
        parts.append("## 说话风格（必须遵守）")
        for line in _SPEECH_STYLE:
            parts.append(f"- {line}")

        # ── 11. 示例对话 ──
        good = _EXAMPLES.get("good", [])
        bad = _EXAMPLES.get("bad", [])
        if good or bad:
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

        # ── 12. 输出格式（末尾强约束）──
        parts.append("")
        parts.append("## 输出格式")
        for line in _OUTPUT_SCHEMA:
            parts.append(line)

        return "\n".join(parts)

    async def _ai_reply(self, user_message: str, session_id: str, user_id: str) -> dict[str, Any]:
        config = LLMConfig.from_mapping()
        system_prompt = self._build_system_prompt(user_id)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self._get_history(session_id))
        # 在用户消息前注入当前时间，确保 LLM 回答时间类问题时正确
        now = datetime.now()
        time_prefix = f"[当前真实时间：{now.strftime('%m月%d日 %H:%M')} 周{'一二三四五六日'[now.weekday()]}]\n"
        messages.append({"role": "user", "content": time_prefix + user_message})

        try:
            return await LLMClient(config).chat_json(messages)
        except Exception:
            text = await LLMClient(config).chat_text(messages)
            return {"text": text, "face_id": "", "send_meme": False}

    async def _send_reply(
        self, ctx: Context, result: dict[str, Any] | list[Any], session_id: str, user_id: str
    ) -> None:
        text, face_id, send_meme = _parse_result(result)

        # ── 毒性过滤：代码层硬拦截，检测到侮辱/贬低/不耐烦语气直接替换 ──
        toxic_match = _check_toxic(text)
        if toxic_match:
            logger.warning(f"毒性拦截: text={text[:50]} pattern={toxic_match}")
            text = _TOXIC_FALLBACK["text"]
            face_id = ""
            send_meme = False

        # ── 事实闸门：检查回复是否包含无依据的编造内容 ──
        user_raw = ctx.text.strip()
        if text and user_raw:
            user_history_msgs = self._history_service.get_recent_user_messages(session_id, limit=20)
            memories = self._memory_service.get_memories(user_id, limit=20)
            social_raw = self._social_memory_service.get_related_memories(user_id, limit=10)
            social_texts = [m["content"] for m in social_raw]

            evidence_text = _build_evidence_text(
                current_user_message=user_raw,
                user_history_messages=user_history_msgs,
                memories=memories,
                social_memories=social_texts,
            )
            unsupported = _check_unsupported_claims(text, evidence_text)
            if unsupported:
                logger.warning(
                    f"事实闸门拦截: text={text[:40]} unsupported={unsupported}"
                )
                rewritten = await _rewrite_unsafe_reply(text, unsupported, user_raw)
                text, face_id, send_meme = _parse_result(rewritten)
                logger.info(f"事实闸门重写: text={text[:40]}")
                # 重写后再过一遍毒性检查
                toxic2 = _check_toxic(text)
                if toxic2:
                    logger.warning(f"重写后仍毒性: text={text[:50]} pattern={toxic2}")
                    text = _TOXIC_FALLBACK["text"]
                    face_id = ""
                    send_meme = False

        # ── 表情包反击：对方连续发表情包时强制回图 ──
        if self._meme_streak >= 2:
            send_meme = True
            logger.info(f"表情包反击: streak={self._meme_streak} 强制回图")

        # 行为决策 → 获取情绪分类
        decision = self._get_behavior_action(user_id)
        emotion = decision.get("emotion", "teasing")

        # 按情绪分类拉取斗图图片（优先本地收藏库）
        meme_url: str | None = None
        meme_mface_data: dict[str, Any] | None = None
        if send_meme:
            fav = await self._meme_service.get_meme_url(emotion, user_id=user_id)
            if fav:
                if fav["type"] == "mface":
                    meme_mface_data = fav["data"]
                else:
                    meme_url = fav["url"]

        # ── 表情包配文策略：有图时默认不发文字 ──
        if (meme_url or meme_mface_data) and text:
            if MEME_CAPTION_MODE == "never":
                text = ""
            elif MEME_CAPTION_MODE == "short":
                stripped = text.strip()
                if len(stripped) > 8:
                    text = ""
                else:
                    text = stripped

        if text or face_id or meme_url or meme_mface_data:
            _log_reply(text, face_id, send_meme, meme_url or "[mface]")
            user_raw = ctx.text.strip()
            self._append_history(session_id, user_raw or "[图片]", text or "[表情包]")

        # 真人化发送：随机延迟、引用回复、长文本分拆
        adapters = getattr(self.runtime, "adapters", [])
        if adapters:
            await _send_human(
                adapter=adapters[0],
                user_id=user_id,
                text=text,
                meme_url=meme_url,
                meme_mface_data=meme_mface_data,
                face_id=face_id,
                event_message=ctx.event.message if hasattr(ctx.event, "message") else None,
            )

        # 记忆提取
        user_raw = ctx.text.strip()
        if user_raw and user_raw != "对方发了一张图片/表情包过来":
            if _should_extract_memory(user_raw):
                memory = await self._extract_memory(user_raw)
                if memory.get("save") and memory.get("memory"):
                    self._memory_service.save_memory(
                        user_id, memory["memory"], importance=memory.get("importance", 0.5)
                    )
                    logger.info(f"记忆保存: {memory['memory'][:40]}")

                    # 社交记忆提取：是否提到了其他人
                    social = await self._extract_social_memory(user_raw)
                    if social.get("save") and social.get("content"):
                        self._social_memory_service.save_social_memory(
                            subject_user=user_id,
                            target_user=str(social.get("target_user", "某人")),
                            relation=str(social.get("relation", "观察")),
                            content=str(social["content"]),
                            importance=float(social.get("importance", 0.5)),
                        )
                        logger.info(f"社交记忆保存: {social['content'][:40]}")
            else:
                logger.debug(f"跳过记忆提取: {user_raw}")

        # 状态更新
        self._mood_service.adjust_energy(REPLY_ENERGY_DELTA)
        self._relation_service.adjust_intimacy(user_id, REPLY_INTIMACY_DELTA)

        # 消息计数 + 反思触发 + 关系推理
        self._msg_counter += 1
        if self._msg_counter >= MESSAGES_BEFORE_REFLECTION:
            self._msg_counter = 0
            await self._generate_reflection(user_id)
            # 反射后触发关系推理（基于累计记忆做高阶推理）
            await self._reasoning_service.generate_reasoning(user_id)

    async def _extract_social_memory(self, user_message: str) -> dict[str, Any]:
        """从用户消息中提取社交关系记忆。"""
        config = LLMConfig.from_mapping()
        prompt = (
            "分析用户消息，判断是否提到了其他人物关系。\n\n"
            "## 保存（save=true）：\n"
            "- 提到了朋友、家人、同学、同事等\n"
            "- 表达了对此人的关心、不满、观察等\n\n"
            "## 不保存（save=false）：\n"
            "- 没有提到其他人\n"
            "- 只是泛泛而谈\n\n"
            "返回 JSON：{\"save\": true/false, \"target_user\": \"被提到的人\", "
            "\"relation\": \"关心/观察/吐槽/帮助\", \"content\": \"关系描述\", \"importance\": 0.1~1.0}\n\n"
            "关系描述要求：简洁概括两人之间的互动或关系，禁止编造。\n"
            "例如：yqy说\"我妹妹最近考试压力很大\" → target_user=妹妹 relation=关心 content=妹妹最近考试压力大\n"
            "用户消息：" + user_message
        )
        try:
            return await LLMClient(config).chat_json(
                [{"role": "user", "content": prompt}]
            )
        except Exception:
            return {"save": False}

    async def _extract_memory(self, user_message: str) -> dict[str, Any]:
        """调用 LLM 从用户消息中提取值得长期记住的事实。"""
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
            "返回 JSON：{\"save\": true/false, \"memory\": \"提炼的事实\", \"importance\": 0.1~1.0}\n"
            "importance 表示事实的重要性：0.5 普通，0.8 重要，1.0 非常重要。\n"
            "用户消息：" + user_message
        )
        try:
            return await LLMClient(config).chat_json(
                [{"role": "user", "content": prompt}]
            )
        except Exception:
            return {"save": False}

    async def _generate_reflection(self, user_id: str) -> None:
        """触发一次反思：根据长期记忆和最近聊天提炼观察/判断。"""
        today_count = self._reflection_service.count_today()
        if today_count >= MAX_REFLECTIONS_PER_DAY:
            logger.debug(f"今日反思已达上限({MAX_REFLECTIONS_PER_DAY})，跳过")
            return

        # 收集上下文
        memories = self._memory_service.get_memories(user_id)
        history = self._history_service.get_recent_history(
            user_id, MAX_HISTORY_TURNS
        )
        rel = self._relation_service.get_or_create_user(user_id)

        memory_text = "\n".join(f"- {m}" for m in memories) if memories else "（暂无长期记忆）"
        history_text = "\n".join(
            f"{h['role']}: {h['content']}" for h in history
        ) if history else "（暂无最近聊天记录）"

        config = LLMConfig.from_mapping()
        prompt = (
            f"你是 YHarvest，你在和一个叫 {rel['nickname']}（身份：{rel['identity']}）的人聊天。\n\n"
            "## 你记得关于这个人的事实：\n"
            f"{memory_text}\n\n"
            "## 最近聊天记录：\n"
            f"{history_text}\n\n"
            "基于以上信息，提炼一条你的「观察/判断」。\n\n"
            "## 要求：\n"
            "- 是总结和判断，不是事实复述\n"
            "- 写成自然的第一人称内部想法，用中文\n"
            "- 像你在日记里写的一句感悟\n"
            "- 禁止编造不存在的事件、经历、细节\n"
            "- 只能基于上面列出的事实和聊天记录做判断\n\n"
            "## 正确例子：\n"
            "- yqy最近对Agent开发特别感兴趣\n"
            "- 妹妹最近学习压力好像有点大\n\n"
            "## 错误例子（这是事实复述，不要这样）：\n"
            "- yqy说他在开发Agent\n"
            "- 妹妹说最近要考试\n"
            "- yqy昨晚又熬夜了（没有依据的编造）\n\n"
            "如果当前信息不足以形成有价值的观察，返回 {\"save\": false}。\n"
            "返回 JSON：{\"save\": true/false, \"reflection\": \"观察内容\", \"importance\": 0.1~1.0}"
        )
        try:
            result = await LLMClient(config).chat_json(
                [{"role": "user", "content": prompt}]
            )
        except Exception:
            logger.warning("反思生成失败：LLM 调用异常")
            return

        if result.get("save") and result.get("reflection"):
            reflection = str(result["reflection"]).strip()
            importance = float(result.get("importance", 0.5))
            self._reflection_service.save_reflection(reflection, importance)
            logger.info(f"反思保存: {reflection[:60]}")

    def _get_behavior_action(self, user_id: str) -> dict[str, str]:
        """获取当前推荐的行为决策。"""
        mood_state = self._mood_service.get_state()
        rel = self._relation_service.get_or_create_user(user_id)
        memories = self._memory_service.get_memories(user_id)
        decision = self._behavior_service.decide_next_action(mood_state, rel, memories)
        logger.info(
            f"Behavior Decision: action={decision['action']} reason={decision['reason']}"
        )
        return decision


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


def _parse_result(result: dict[str, Any] | list[Any]) -> tuple[str, str, bool]:
    text = ""
    face_id = ""
    send_meme = False

    src: dict[str, Any] = {}
    if isinstance(result, dict):
        src = result
    elif isinstance(result, list) and len(result) > 0:
        src = result[0] if isinstance(result[0], dict) else {}

    text = str(src.get("text", ""))
    raw_face = str(src.get("face_id", ""))
    face_id = raw_face.strip() if raw_face.strip().isdigit() else ""
    send_meme = bool(src.get("send_meme", False))

    return text, face_id, send_meme


def _check_toxic(text: str) -> str | None:
    """检查回复是否包含毒性内容，返回匹配到的模式，无毒返回 None。"""
    if not text:
        return None
    for pat in _TOXIC_PATTERNS:
        if pat in text:
            return pat
    return None


def _log_reply(text: str, face_id: str, send_meme: bool, meme_url: str | None) -> None:
    parts = []
    if send_meme:
        parts.append("图片" + (f"[{meme_url[:40]}]" if meme_url else "[拉取失败]"))
    if text:
        parts.append(f"文字[{text[:40]}]")
    if face_id:
        parts.append(f"表情[{face_id}]")
    label = " + ".join(parts) if parts else "空"
    logger.info(f"AI回复: {label}")


def _is_media_segment(seg: dict[str, Any]) -> bool:
    seg_kind = seg.get("kind", "")
    if seg_kind == "mface":
        return True
    data_keys = set(seg.get("data", {}).keys())
    if data_keys & {"file", "url"}:
        return True
    if data_keys & {"chainCount", "resultId"}:
        return True
    return False


# ═══════════════════════════════════════════
#  事实闸门：代码层拦截无依据的编造内容
# ═══════════════════════════════════════════

def _check_unsupported_claims(text: str, evidence_text: str) -> list[str]:
    """检查回复是否包含无依据的高风险触发词。

    Args:
        text: AI 回复文本
        evidence_text: 事实证据文本（含当前用户消息、历史 user 消息、记忆等）

    Returns:
        在 text 中出现但不在 evidence_text 中的风险触发词列表
    """
    if not text:
        return []
    unsupported: list[str] = []
    for trigger in _HIGH_RISK_TRIGGERS:
        if trigger in text and trigger not in evidence_text:
            unsupported.append(trigger)
    return unsupported


def _build_evidence_text(
    current_user_message: str,
    user_history_messages: list[str],
    memories: list[str],
    social_memories: list[str],
) -> str:
    """构建事实证据文本（仅 user 方信息，不含 assistant 历史）。"""
    parts: list[str] = [current_user_message]
    parts.extend(user_history_messages)
    parts.extend(memories)
    parts.extend(social_memories)
    return "\n".join(parts)


async def _rewrite_unsafe_reply(
    original_text: str,
    unsupported_words: list[str],
    current_user_message: str,
) -> dict[str, Any]:
    """调用 LLM 重写包含无依据事实的回复。

    Args:
        original_text: 原始 AI 回复
        unsupported_words: 无依据的触发词
        current_user_message: 当前用户消息

    Returns:
        重写后的 reply dict，失败则返回兜底回复
    """
    config = LLMConfig.from_mapping()
    prompt = (
        f"{_REWRITE_PROMPT}\n\n"
        f"当前用户消息：{current_user_message}\n\n"
        f"以下回复包含无依据的细节（{', '.join(unsupported_words)}），请重写：\n"
        f"原始回复：{original_text}\n\n"
        "重写后的 JSON："
    )
    try:
        return await LLMClient(config).chat_json(
            [{"role": "user", "content": prompt}]
        )
    except Exception:
        logger.warning("事实闸门重写失败，使用兜底回复")
        return dict(_FALLBACK_REPLY)
