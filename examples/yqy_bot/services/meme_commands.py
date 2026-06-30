"""收藏表情指令处理服务。

使用 NapCat API 管理 QQ 客户端本地表情库：
- 存表情：下载图片 → /add_custom_face
- 发表情：/fetch_custom_face → 用 image 发送
- 删表情：/delete_custom_face
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from iamai import Message
from loguru import logger

from .config_service import MEME_SAVE_KEYWORDS, MEME_SEND_KEYWORDS
from .human_behavior import _image_segment

if TYPE_CHECKING:
    from iamai import Context
    from iamai.adapter import Adapter

    from .meme_service import MemeService


class MemeCommandHandler:
    """收藏表情指令处理器：使用 NapCat API。"""

    def __init__(self, meme_service: "MemeService") -> None:
        self._meme_service = meme_service
        # 等待模式状态：user_id → {"emotion", "tags"}
        self._pending_meme_save: dict[str, dict[str, str]] = {}

    def is_meme_command(self, text: str) -> bool:
        """判断文本是否包含表情指令关键词。"""
        # 发送指令
        if any(kw in text for kw in MEME_SEND_KEYWORDS):
            return True
        # 存表情指令
        if text in MEME_SAVE_KEYWORDS:
            return True
        # 带情绪标签的存表情指令
        for kw in ("存表情 ", "收藏表情 ", "学习表情包 "):
            if text.startswith(kw):
                return True
        # 删除表情指令
        if text.startswith("删表情 ") or text == "删表情":
            return True
        return False

    async def handle_command(
        self,
        ctx: "Context",
        user_id: str,
        text: str,
        segments: list[dict[str, Any]],
        reply_target: dict[str, str],
    ) -> bool:
        """处理表情指令。"""
        adapters = getattr(ctx.runtime, "adapters", [])
        if not adapters:
            logger.warning("MemeCommandHandler: 无适配器可用")
            return False

        adapter: "Adapter" = adapters[0]

        # ── 测试指令：随机发一张 QQ 收藏表情 ──
        if any(kw in text for kw in MEME_SEND_KEYWORDS):
            await self._send_random_qq_favorite(ctx, adapter, user_id, reply_target)
            return True

        # ── 删除表情指令 ──
        if text.startswith("删表情 ") or text == "删表情":
            await self._handle_delete_command(ctx, adapter, user_id, text, reply_target)
            return True

        # ── 「存表情」指令：进入等待模式 ──
        if text in MEME_SAVE_KEYWORDS:
            self._pending_meme_save[user_id] = {"emotion": "default", "tags": ""}
            await self._send_text_reply(
                ctx,
                user_id,
                "把你要我记住的表情发过来，我存到 QQ 收藏里。",
                reply_target,
            )
            return True

        # ── 「存表情 xxx」带情绪标签 ──
        for kw in ("存表情 ", "收藏表情 ", "学习表情包 "):
            if text.startswith(kw):
                emotion = text[len(kw) :].strip()
                self._pending_meme_save[user_id] = {"emotion": emotion, "tags": emotion}
                await self._send_text_reply(
                    ctx, user_id, "好的，下一张图会添加到 QQ 收藏。", reply_target
                )
                return True

        # ── 等待模式：收到图片 → 保存到 QQ ──
        if user_id in self._pending_meme_save:
            img_result = self._extract_image_url(segments)
            if img_result:
                self._pending_meme_save.pop(user_id)
                img_url, seg_type = img_result

                # 调用 NapCat API 保存
                success = await self._meme_service.save_from_url(adapter, img_url)
                if success:
                    await self._send_text_reply(
                        ctx, user_id, "存好了，在 QQ 收藏表情里能看到。", reply_target
                    )
                else:
                    await self._send_text_reply(
                        ctx, user_id, "这个表情我没存上，可能下载失败。", reply_target
                    )
                return True
            else:
                # 没收到图片，取消等待
                self._pending_meme_save.pop(user_id, None)
                await self._send_text_reply(
                    ctx, user_id, "没看到图片，不存了。", reply_target
                )
                return True

        return False

    async def _send_random_qq_favorite(
        self,
        ctx: "Context",
        adapter: "Adapter",
        user_id: str,
        reply_target: dict[str, str],
    ) -> None:
        """从 QQ 收藏表情库随机发一张。"""
        urls = await self._meme_service.get_qq_favorites(adapter, count=48)
        if not urls:
            await self._send_text_reply(
                ctx, user_id, "QQ 收藏表情里还没有表情，先存几张。", reply_target
            )
            return

        import random

        url = random.choice(urls)
        segment = _image_segment(url)
        msg = Message([segment])
        await adapter.send_message(msg, target=reply_target)
        logger.info(f"[测试指令] 发送QQ收藏表情 → {user_id}")

    async def _handle_delete_command(
        self,
        ctx: "Context",
        adapter: "Adapter",
        user_id: str,
        text: str,
        reply_target: dict[str, str],
    ) -> None:
        """处理删除表情指令。"""
        # 获取收藏表情详情
        records = await self._meme_service.get_qq_favorites_detail(adapter, count=48)
        if not records:
            await self._send_text_reply(
                ctx, user_id, "QQ 收藏表情里没有可删除的表情。", reply_target
            )
            return

        # 解析删除索引（如果用户指定了）
        if text == "删表情":
            # 显示前 10 个表情供选择
            lines = ["要删除哪个？回复「删表情 序号」："]
            for i, rec in enumerate(records[:10]):
                res_id = rec.get("resId") or rec.get("id") or ""
                url = rec.get("url") or rec.get("emoji_url") or ""
                lines.append(f"{i+1}. {url[:50]}...")
            await self._send_text_reply(ctx, user_id, "\n".join(lines), reply_target)
            return

        # 删除指定序号
        try:
            index = int(text.split(" ", 1)[-1].strip()) - 1
            if 0 <= index < len(records):
                res_id = records[index].get("resId") or records[index].get("id") or ""
                if res_id:
                    success = await self._meme_service.delete_from_qq(adapter, res_id)
                    if success:
                        await self._send_text_reply(
                            ctx,
                            user_id,
                            f"删除成功，第 {index+1} 个表情已移除。",
                            reply_target,
                        )
                    else:
                        await self._send_text_reply(
                            ctx, user_id, "删除失败，可能 API 出错。", reply_target
                        )
                else:
                    await self._send_text_reply(
                        ctx, user_id, "找不到这个表情的资源 ID。", reply_target
                    )
            else:
                await self._send_text_reply(
                    ctx,
                    user_id,
                    f"序号超出范围，当前有 {len(records)} 个表情。",
                    reply_target,
                )
        except ValueError:
            await self._send_text_reply(
                ctx,
                user_id,
                "请用「删表情 序号」格式，比如「删表情 1」。",
                reply_target,
            )

    async def _send_text_reply(
        self, ctx: "Context", user_id: str, text: str, reply_target: dict[str, str]
    ) -> None:
        """发送纯文本回复。"""
        adapters = getattr(ctx.runtime, "adapters", [])
        if not adapters:
            return

        adapter: "Adapter" = adapters[0]
        msg = Message([{"type": "text", "data": {"text": text}}])
        await adapter.send_message(msg, target=reply_target)
        logger.info(f"[指令回复] → {user_id} text={text[:40]}")

    @staticmethod
    def _extract_image_url(segments: list[dict[str, Any]]) -> tuple[str, str] | None:
        """从消息段中提取图片 URL。

        支持: image, mface, marketface 类型。
        """
        for seg in segments:
            seg_kind = seg.get("kind", "")
            data = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}

            # mface：提取 url 字段
            if seg_kind == "mface":
                url = data.get("url") or data.get("emoji_url") or ""
                if url:
                    logger.info(f"[收藏表情] mface url={url[:60]}")
                    return (str(url), "mface")
                logger.warning("[收藏表情] mface 缺少 url 字段")
                return None

            if seg_kind in ("image", "marketface"):
                url = data.get("url") or data.get("file") or data.get("path") or ""
                if url:
                    logger.info(f"[收藏表情] image url={url[:60]}")
                    return (str(url), "image")

        return None
