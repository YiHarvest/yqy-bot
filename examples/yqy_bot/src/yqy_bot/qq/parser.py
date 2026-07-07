from __future__ import annotations

from typing import Any, TYPE_CHECKING

from yqy_bot.core.models import ParsedMessage


def parse_message_input(payload: dict[str, Any]) -> ParsedMessage:
    """解析原始消息载荷为结构化的 ParsedMessage 对象。

    从 OneBot11 协议格式的消息载荷中提取关键信息，
    包括发送者、消息内容、@ 信息、图片等。

    Args:
        payload: 原始消息载荷字典，符合 OneBot11 协议格式

    Returns:
        解析后的 ParsedMessage 对象，包含所有提取的消息信息
    """
    raw_event = _extract_raw_event(payload)
    self_id = _string(raw_event.get("self_id") or payload.get("self_id"))
    user_id = _string(raw_event.get("user_id") or payload.get("user_id"))
    group_id = _string(raw_event.get("group_id") or payload.get("group_id") or "")
    message_id = _string(raw_event.get("message_id") or payload.get("message_id") or payload.get("event_id"))
    message_type = _string(raw_event.get("message_type") or payload.get("message_type"))
    sender = raw_event.get("sender") if isinstance(raw_event.get("sender"), dict) else {}
    sender_nickname = _string(sender.get("nickname") if isinstance(sender, dict) else payload.get("sender_nickname"))
    sender_card = _string(sender.get("card") if isinstance(sender, dict) else payload.get("sender_card"))
    raw_segments = _extract_segments(raw_event, payload)

    text_parts: list[str] = []
    mention_user_ids: list[str] = []
    is_at_bot = False
    reply_message_id = ""
    has_image = False

    for segment in raw_segments:
        seg_type = str(segment.get("type", ""))
        data = segment.get("data") or {}
        if seg_type == "text":
            text_parts.append(_string(data.get("text", "")))
        elif seg_type == "at":
            qq = _string(data.get("qq", ""))
            if qq and qq != "all":
                mention_user_ids.append(qq)
            if self_id and qq == self_id:
                is_at_bot = True
        elif seg_type == "reply":
            reply_message_id = _string(data.get("id", ""))
        elif seg_type == "image":
            has_image = True

    text = " ".join(part for part in text_parts if part).strip()
    if not text:
        text = _string(payload.get("text", "")).strip() or _string(raw_event.get("raw_message", "")).strip()

    is_group = message_type == "group" or bool(group_id)
    session_id = f"group:{group_id}" if is_group else f"private:{user_id}"
    sender_display_name = sender_card or sender_nickname or user_id
    return ParsedMessage(
        event_id=_string(payload.get("event_id") or payload.get("id") or message_id),
        adapter=_string(payload.get("adapter") or "onebot11"),
        platform=_string(payload.get("platform") or "qq"),
        self_id=self_id,
        user_id=user_id or "unknown",
        group_id=group_id,
        message_id=message_id,
        message_type=message_type or ("group" if is_group else "private"),
        sender_nickname=sender_nickname,
        sender_card=sender_card,
        text=text,
        raw=raw_event,
        segments=raw_segments,
        raw_segments=raw_segments,
        is_at_bot=is_at_bot,
        mention_user_ids=mention_user_ids,
        has_image=has_image,
        reply_message_id=reply_message_id,
        sender_display_name=sender_display_name,
        is_group=is_group,
        is_private=not is_group,
    )


def parse_event(event: "EventLike") -> ParsedMessage:
    """从 iamai Event 对象解析消息为 ParsedMessage。

    Args:
        event: iamai 事件对象，符合 EventLike 协议

    Returns:
        解析后的 ParsedMessage 对象
    """
    payload = {
        "event_id": event.id,
        "adapter": event.adapter,
        "platform": event.platform,
        "self_id": event.self_id,
        "user_id": event.user_id,
        "group_id": getattr(event, "channel_id", None) or getattr(event, "guild_id", None) or "",
        "message_id": event.id,
        "message_type": "group" if getattr(event, "channel_id", None) or getattr(event, "guild_id", None) else "private",
        "sender_nickname": "",
        "sender_card": "",
        "message": event.message.segments,
        "raw_event": event.raw,
    }
    return parse_message_input(payload)


def _extract_raw_event(payload: dict[str, Any]) -> dict[str, Any]:
    """从载荷中提取原始事件字典。

    尝试从多个可能的字段中获取原始事件数据。

    Args:
        payload: 消息载荷字典

    Returns:
        原始事件字典，如果不存在则返回空字典
    """
    raw_event = payload.get("raw_event")
    if isinstance(raw_event, dict):
        return raw_event
    raw_event = payload.get("raw")
    if isinstance(raw_event, dict):
        return raw_event
    return {}


def _extract_segments(raw_event: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从事件数据中提取消息段列表。

    消息段可能存在于 raw_event.message 或 payload.message/segments 字段。

    Args:
        raw_event: 原始事件字典
        payload: 消息载荷字典

    Returns:
        消息段列表，每个元素为字典格式的消息段
    """
    source = raw_event.get("message")
    if source is None:
        source = payload.get("message", payload.get("segments", []))
    if isinstance(source, list):
        return [dict(item) for item in source if isinstance(item, dict)]
    return []


def _string(value: Any) -> str:
    """将任意值转换为字符串，None 值转为空字符串。

    Args:
        value: 任意类型的值

    Returns:
        字符串值，如果输入为 None 则返回空字符串
    """
    return "" if value is None else str(value)


if TYPE_CHECKING:
    from typing import Protocol

    class EventLike(Protocol):
        id: Any
        adapter: Any
        platform: Any
        user_id: Any
        self_id: Any
        raw: dict[str, Any]
        message: Any
