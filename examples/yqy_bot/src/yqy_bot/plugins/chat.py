from __future__ import annotations

import logging

from iamai import Context, Message, Plugin, message_handler

from yqy_bot.core.pipeline import ChatPipeline
from yqy_bot.qq.sender import build_message_segments

LOGGER = logging.getLogger(__name__)


class _ContextSender:
    def __init__(self, ctx: Context) -> None:
        """初始化上下文发送器。

        Args:
            ctx: iamai 上下文对象
        """
        self._ctx = ctx

    async def send(self, parsed, response) -> None:
        """发送回复消息到 QQ 平台。

        Args:
            parsed: 解析后的消息对象
            response: 生成的回复对象
        """
        segments = build_message_segments(response)
        if not segments:
            return
        await self._ctx.reply(Message(segments))


class ChatPlugin(Plugin):
    name = "chat"
    description = "Passive QQ chat pipeline for YQY_BOT MVP."
    _pipeline_started: bool = False

    async def startup(self) -> None:
        """启动聊天插件，初始化聊天管道并启动后台任务。"""
        await self._ensure_pipeline()

    async def shutdown(self) -> None:
        """关闭聊天插件，停止后台任务并清理资源。"""
        pipeline = self.runtime.state.pop("yqy_bot.pipeline", None)
        if pipeline is not None:
            await pipeline.shutdown()
        self._pipeline_started = False

    async def _ensure_pipeline(self) -> ChatPipeline:
        """确保 pipeline 有效，检测数据库连接状态。

        如果 pipeline 不存在或数据库连接已关闭，重新创建。

        Returns:
            有效 ChatPipeline 实例
        """
        pipeline = self.runtime.state.get("yqy_bot.pipeline")
        need_recreate = False
        if pipeline is not None:
            # 检查数据库连接是否有效
            try:
                # 简单查询测试连接
                pipeline.bundle.database.fetchone("SELECT 1")
            except Exception as e:
                LOGGER.warning("[ChatPlugin] 数据库连接已关闭，重新创建 pipeline: %s", e)
                need_recreate = True
        else:
            need_recreate = True

        if need_recreate:
            # 关闭旧 pipeline（如果存在）
            if pipeline is not None:
                try:
                    await pipeline.shutdown()
                except Exception:
                    pass
            LOGGER.info("[ChatPlugin] 创建新的 ChatPipeline")
            pipeline = ChatPipeline.from_runtime(self.runtime.base_path)
            self.runtime.state["yqy_bot.pipeline"] = pipeline
            await pipeline.startup()
            self._pipeline_started = True

        return pipeline

    @message_handler(priority=50)
    async def passive_chat(self, ctx: Context) -> None:
        """处理被动 QQ 聊天消息，通过聊天管道处理事件。

        Args:
            ctx: iamai 上下文对象，包含事件信息和回复方法
        """
        pipeline = await self._ensure_pipeline()
        await pipeline.process_message(ctx.event.to_dict(), sender=_ContextSender(ctx))
