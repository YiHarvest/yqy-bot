from __future__ import annotations

from dataclasses import dataclass

from yqy_bot.core.models import ParsedMessage


@dataclass(slots=True)
class HumanBehavior:
    """人类行为模拟类，用于模拟真实用户的打字延迟。"""

    private_min_delay: float = 0.1
    private_max_delay: float = 0.3
    group_min_delay: float = 0.2
    group_max_delay: float = 0.5

    async def typing_delay(self, parsed: ParsedMessage, reply_text: str) -> float:
        """计算模拟打字等待时长。

        根据消息类型（群聊/私聊）和回复长度计算合理的打字延迟，
        使机器人回复更接近真实用户行为。

        Args:
            parsed: 解析后的消息对象
            reply_text: 回复文本内容

        Returns:
            等待秒数，群聊被 @ 时会额外增加 0.2 秒
        """
        length = len(reply_text.strip())
        if parsed.is_group:
            base = self.group_min_delay if length <= 20 else self.group_max_delay
        else:
            base = self.private_min_delay if length <= 20 else self.private_max_delay
        if parsed.is_group and parsed.is_at_bot:
            return min(base + 0.2, self.group_max_delay)
        return base
