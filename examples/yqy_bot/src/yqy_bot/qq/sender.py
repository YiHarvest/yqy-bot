from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yqy_bot.core.models import GeneratedResponse
from .napcat_tools import NapCatTools


def build_message_segments(response: GeneratedResponse) -> list[dict[str, Any]]:
    """构建 QQ 消息段列表，包含文本、表情、图片等元素。

    Args:
        response: 生成的回复对象

    Returns:
        消息段列表，每个元素为字典形式的 QQ 消息段
    """
    normalized = response.normalized()
    segments: list[dict[str, Any]] = []
    if normalized.reply_to_message_id:
        segments.append({"type": "reply", "data": {"id": normalized.reply_to_message_id}})
    if normalized.at_user_id:
        segments.append({"type": "at", "data": {"qq": normalized.at_user_id}})
    if normalized.text:
        segments.append({"type": "text", "data": {"text": normalized.text}})
    if normalized.send_face and normalized.face_id:
        segments.append({"type": "face", "data": {"id": normalized.face_id}})
    if normalized.send_mface and normalized.mface:
        payload = normalize_mface_payload(normalized.mface)
        if payload:
            segments.append({"type": "mface", "data": payload})
    if normalized.send_image and normalized.image_url:
        segments.append({"type": "image", "data": {"file": normalized.image_url}})
    return segments


def normalize_mface_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """规范化小表情（mface）载荷格式。

    Args:
        payload: 原始小表情载荷字典

    Returns:
        规范化后的小表情载荷字典，所有字段均为字符串类型
    """
    return {
        "emoji_id": str(payload.get("emoji_id", "")).strip(),
        "emoji_package_id": str(payload.get("emoji_package_id", "")).strip(),
        "key": str(payload.get("key", "")).strip(),
        "summary": str(payload.get("summary", "")).strip(),
    }


@dataclass(slots=True)
class QQSender:
    tools: NapCatTools

    async def send(self, parsed, response: GeneratedResponse) -> dict[str, Any]:
        """发送回复消息到 QQ 平台。

        Args:
            parsed: 解析后的消息对象，包含目标信息
            response: 生成的回复对象，包含消息内容

        Returns:
            发送结果字典，包含消息 ID 等信息
        """
        segments = build_message_segments(response)
        if parsed.is_group:
            return await self.tools.send_group_msg(parsed.group_id, segments)
        return await self.tools.send_private_msg(parsed.user_id, segments)
