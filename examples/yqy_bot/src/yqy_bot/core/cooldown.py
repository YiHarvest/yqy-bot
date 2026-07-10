from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ProjectConfig
from .models import ParsedMessage
from yqy_bot.storage.repositories import Repositories


@dataclass(slots=True)
class SendCooldown:
    config: ProjectConfig
    repos: Repositories

    def can_reply(self, parsed: ParsedMessage, *, now: float) -> bool:
        """检查是否可以回复消息（冷却时间是否已过）。

        Args:
            parsed: 解析后的消息对象
            now: 当前时间戳（秒）

        Returns:
            如果冷却时间已过可以回复则返回 True，否则返回 False
        """
        state = self.repos.get_cooldown_state(parsed.chat_key)
        if state is None:
            return True
        last_reply_at = float(state.get("last_reply_at", 0.0))
        cooldown = self._cooldown_seconds(parsed)
        return now - last_reply_at >= cooldown

    def mark_replied(
        self, parsed: ParsedMessage, *, now: float, group_heat_state: str
    ) -> None:
        """标记已回复状态，更新冷却时间和热度状态。

        Args:
            parsed: 解析后的消息对象
            now: 当前时间戳（秒）
            group_heat_state: 群热度状态（quiet/active/hot/flood）
        """
        state = self.repos.get_cooldown_state(parsed.chat_key)
        recent_10s_count = int(state.get("recent_10s_count", 0)) if state else 0
        recent_30s_count = int(state.get("recent_30s_count", 0)) if state else 0
        last_history_backfill_at = (
            float(state.get("last_history_backfill_at", 0.0)) if state else 0.0
        )
        self.repos.upsert_cooldown_state(
            parsed=parsed,
            last_message_at=now,
            last_reply_at=now,
            recent_10s_count=recent_10s_count,
            recent_30s_count=recent_30s_count,
            heat_state=group_heat_state,
            last_history_backfill_at=last_history_backfill_at,
        )

    def _cooldown_seconds(self, parsed: ParsedMessage) -> int:
        """获取消息对应的冷却时间秒数。

        Args:
            parsed: 解析后的消息对象

        Returns:
            群聊或私聊对应的冷却时间秒数
        """
        return (
            self.config.bot.group_cooldown_seconds
            if parsed.is_group
            else self.config.bot.private_cooldown_seconds
        )


@dataclass(slots=True)
class GroupHeat:
    config: ProjectConfig
    repos: Repositories

    def update(self, parsed: ParsedMessage, *, now: float) -> str:
        """更新群热度状态并返回当前热度等级。

        Args:
            parsed: 解析后的消息对象
            now: 当前时间戳（秒）

        Returns:
            热度状态字符串（quiet/active/hot/flood），私聊始终返回 "quiet"
        """
        if not parsed.is_group:
            return "quiet"
        state = self.repos.get_cooldown_state(parsed.chat_key)
        if state is None:
            state = {
                "last_message_at": now,
                "last_reply_at": 0.0,
                "recent_10s_count": 0,
                "recent_30s_count": 0,
                "heat_state": "quiet",
            }
        else:
            state = dict(state)
        recent_10s_count = self._count_recent(state, now, window_seconds=10)
        recent_30s_count = self._count_recent(state, now, window_seconds=30)
        heat_state = self._heat_state(recent_10s_count, recent_30s_count)
        self.repos.upsert_cooldown_state(
            parsed=parsed,
            last_message_at=now,
            last_reply_at=float(state.get("last_reply_at", 0.0)),
            recent_10s_count=recent_10s_count,
            recent_30s_count=recent_30s_count,
            heat_state=heat_state,
            last_history_backfill_at=float(state.get("last_history_backfill_at", 0.0)),
        )
        return heat_state

    def _count_recent(
        self, state: dict[str, Any], now: float, *, window_seconds: int
    ) -> int:
        """计算指定时间窗口内的消息计数。

        Args:
            state: 当前冷却状态字典
            now: 当前时间戳（秒）
            window_seconds: 时间窗口秒数（10 或 30）

        Returns:
            时间窗口内的消息计数
        """
        last_message_at = float(state.get("last_message_at", now))
        recent_10s_count = int(state.get("recent_10s_count", 0))
        recent_30s_count = int(state.get("recent_30s_count", 0))
        if window_seconds == 10:
            if now - last_message_at > 10:
                return 1
            return recent_10s_count + 1
        if now - last_message_at > 30:
            return 1
        return recent_30s_count + 1

    def _heat_state(self, recent_10s_count: int, recent_30s_count: int) -> str:
        """根据消息频率判断群热度状态。

        Args:
            recent_10s_count: 最近 10 秒消息数
            recent_30s_count: 最近 30 秒消息数

        Returns:
            热度状态字符串（quiet/active/hot/flood）
        """
        bot = self.config.bot
        if recent_30s_count >= bot.flood_threshold:
            return "flood"
        if (
            recent_30s_count >= bot.hot_threshold
            or recent_10s_count >= bot.hot_threshold
        ):
            return "hot"
        if (
            recent_30s_count >= bot.active_threshold
            or recent_10s_count >= bot.active_threshold
        ):
            return "active"
        return "quiet"
