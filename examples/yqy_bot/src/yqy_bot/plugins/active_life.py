"""主动行为插件：根据情绪、关系、长期记忆，YHarvest 主动联系用户。
配置从 config_service.py 加载。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from iamai import Message, Plugin
from loguru import logger

# 路径配置
from services.config_service import PROJECT_ROOT
from services.napcat_api import NapCatAPI

# 主动行为配置
from services.config_service import (
    TICK_INTERVAL,
    LONELINESS_THRESHOLD,
    ENERGY_THRESHOLD,
    COOLDOWN_HOURS,
    RECENT_HISTORY_TURNS,
    BLOCKED_TARGETS,
)

# 主动行为后状态变化
from services.config_service import (
    POST_ACTION_ENERGY_DELTA,
    POST_ACTION_LONELINESS_DELTA,
)

sys.path.insert(0, str(PROJECT_ROOT))

# ── 热重载兼容：仅在 reload 时清空 services 缓存 ──
_IMPORTED = globals().get("_IMPORTED", False)
if _IMPORTED:
    for _mod in list(sys.modules):
        if _mod.startswith("services."):
            del sys.modules[_mod]
_IMPORTED = True


class ActiveLifePlugin(Plugin):
    """每10分钟检查一次状态，孤独/精力允许时主动联系用户。"""

    name = "active_life"
    description = "YHarvest 主动行为系统。"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from services.active_chat_generator import ActiveChatGenerator
        from services.behavior import BehaviorService
        from services.db import init_db
        from services.history import HistoryService
        from services.human_behavior import PokeCooldown
        from services.meme_service import MemeService
        from services.memory import MemoryService
        from services.mood import MoodService
        from services.reflection import ReflectionService
        from services.relation import RelationshipService
        from services.social_memory import SocialMemoryService

        init_db()
        self._mood = MoodService()
        self._rel = RelationshipService()
        self._mem = MemoryService()
        self._social_mem = SocialMemoryService()
        self._reflection = ReflectionService()
        self._history = HistoryService()
        self._behavior = BehaviorService()
        self._meme_service = MemeService()
        self._chat_gen = ActiveChatGenerator()
        self._poke_cooldown = PokeCooldown()

        self._task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        """启动后台循环。"""
        self._task = asyncio.create_task(self._loop())
        logger.info(f"主动行为引擎已启动（每{TICK_INTERVAL}秒检查一次）")

    async def shutdown(self) -> None:
        """停止后台循环。"""
        if self._task:
            self._task.cancel()
        logger.info("主动行为引擎已停止")

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(TICK_INTERVAL)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("主动行为引擎出错")

    async def _tick(self) -> None:
        """执行一次状态检查。"""
        state = self._mood.get_state()
        loneliness = state["loneliness"]
        energy = state["energy"]
        mood_val = state["mood"]

        if loneliness <= LONELINESS_THRESHOLD:
            return
        if energy <= ENERGY_THRESHOLD:
            logger.info(f"Active check: 精力不足({energy})，跳过")
            return

        target = self._find_best_target()
        if target is None:
            return

        user_id = target["user_id"]

        if self._is_cooldown(user_id):
            return

        memories = self._mem.get_memories(user_id)
        decision = self._behavior.decide_next_action(state, target, memories)
        action = decision["action"]
        reason = decision["reason"]

        if action == "silent":
            logger.info("Active check: 行为决策=silent, 跳过")
            return

        logger.info(
            f"Active Behavior: target={target['nickname']}({user_id}) "
            f"action={action} reason={reason} "
            f"mood={mood_val} loneliness={loneliness} energy={energy}"
        )

        try:
            if action == "chat":
                await self._do_active_chat(user_id, target, memories)
            elif action == "meme":
                await self._do_active_meme(user_id)
            elif action == "poke":
                await self._do_active_poke(user_id)
        except Exception:
            logger.exception(f"主动行为执行失败: action={action} target={user_id}")
            return

        self._log_action(user_id, action)

        self._mood.adjust_energy(POST_ACTION_ENERGY_DELTA)
        self._mood.adjust_loneliness(POST_ACTION_LONELINESS_DELTA)

    # ── 目标选择 ──

    def _find_best_target(self) -> dict[str, Any] | None:
        """返回好感度最高的用户（排除黑名单）。"""
        users = self._rel.get_all_users()
        for user in users:
            if user["user_id"] not in BLOCKED_TARGETS:
                return user
        return None

    def _is_cooldown(self, user_id: str) -> bool:
        """检查该用户是否在冷却期内。"""
        last = self._rel.last_active_action(user_id)
        if last is None:
            return False
        try:
            last_dt = datetime.fromisoformat(last).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False
        return datetime.now(timezone.utc) - last_dt < timedelta(hours=COOLDOWN_HOURS)

    # ── 行为执行 ──

    async def _do_active_chat(
        self, user_id: str, target: dict[str, Any], memories: list[str]
    ) -> None:
        """使用 ActiveChatGenerator 生成并发送主动聊天消息。"""
        reflections = self._reflection.get_recent(user_id)
        history = self._history.get_recent_history_for_user(user_id, RECENT_HISTORY_TURNS)
        social_memories = self._social_mem.get_related_memories(user_id)

        text = await self._chat_gen.generate(
            nickname=target["nickname"],
            identity=target["identity"],
            memories=memories,
            reflections=reflections,
            history=history,
            social_memories=social_memories,
        )

        if not text:
            return

        msg = Message()
        msg.append_text(text)
        await self._send(user_id, msg)

    async def _do_active_meme(self, user_id: str) -> None:
        """发送主动斗图表情包，按当前情绪选择分类。"""
        adapters = getattr(self.runtime, "adapters", [])
        if not adapters:
            return

        state = self._mood.get_state()
        emotion = self._behavior.classify_emotion(state)
        fav = await self._meme_service.get_meme_url(NapCatAPI.from_adapter(adapters[0]), emotion)
        if not fav:
            return
        msg = Message()
        msg.append("image", file=fav["url"])  # 统一用 image 类型
        await self._send(user_id, msg)

    async def _do_active_poke(self, user_id: str) -> None:
        """发送戳一戳（24h冷却）。"""
        if not self._poke_cooldown.can_poke(user_id):
            logger.info(f"Active poke: {user_id} 冷却中，跳过")
            return
        await self._call_api("send_poke", user_id=user_id)
        self._poke_cooldown.record_poke(user_id)

    # ── 消息投递 ──

    async def _send(self, user_id: str, message: Message) -> None:
        """通过 OneBot 适配器发送消息。"""
        adapters = self.runtime.adapters
        if not adapters:
            logger.warning("Active send: 没有可用适配器")
            return
        api = NapCatAPI.from_adapter(adapters[0])
        try:
            await api.send_safe_message({"user_id": user_id}, message)
            logger.info(f"Active send: → {user_id} type=msg")
        except Exception:
            logger.exception(f"Active send 失败: {user_id}")

    async def _call_api(self, action: str, **params: Any) -> Any:
        """调用 OneBot 适配器 API。"""
        adapters = self.runtime.adapters
        if not adapters:
            return None
        adapter = adapters[0]
        try:
            api = NapCatAPI.from_adapter(adapter)
            if action == "send_poke":
                return await api.send_poke(str(params.get("user_id", "")))
            return await api.call_api(action, **params)
        except Exception:
            logger.exception(f"Active call_api 失败: {action} {params}")
            return None

    def _log_action(self, user_id: str, action: str) -> None:
        """记录主动行为的落库状态。"""
        try:
            self._rel.update_last_active_time(user_id)
            logger.info(f"Active log: user={user_id} action={action} last_active_time updated")
        except Exception:
            logger.exception(f"Active log 失败: user={user_id} action={action}")
