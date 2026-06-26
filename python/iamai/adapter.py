"""适配器抽象基类，定义入站事件接收与出站消息发送的统一接口。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .event import Event
from .message import Message


class Adapter(ABC):
    """iamai 与外部协议/运行时之间的传输桥梁。"""

    name = "adapter"

    def __init__(self, runtime: "Runtime", config: dict[str, Any] | None = None) -> None:
        self.runtime = runtime
        self.config = config or {}
        self.logger = logging.getLogger(f"iamai.adapter.{self.name}")

    @abstractmethod
    async def start(self) -> None:
        """启动适配器，开始接收外部事件。"""
        raise NotImplementedError

    async def close(self) -> None:
        """运行时关闭前清理适配器资源。"""
        return None

    @abstractmethod
    async def send_message(
        self,
        message: Message,
        *,
        event: Event | None = None,
        target: Any | None = None,
    ) -> Any:
        """向指定的目标或事件上下文发送消息。"""
        raise NotImplementedError

    async def call_api(self, action: str, **params: Any) -> Any:
        """调用适配器专属的 API 动作。"""
        raise RuntimeError(f"adapter {self.name!r} does not expose call_api")

    async def emit(self, event: Event) -> None:
        """将标准化后的事件提交到运行时调度管道。"""
        await self.runtime.dispatch(event, self)


if TYPE_CHECKING:
    from .runtime import Runtime
