from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .napcat_client import NapCatClient


@dataclass(slots=True)
class NapCatTools:
    client: NapCatClient

    async def get_msg(self, message_id: str) -> dict[str, Any]:
        """获取指定消息的详细信息。

        Args:
            message_id: 消息 ID

        Returns:
            消息详情字典，包含消息内容、发送者等信息
        """
        return await self.client.call_action("get_msg", {"message_id": message_id})

    async def get_group_msg_history(
        self, group_id: str, *, message_seq: int = 0, count: int = 20
    ) -> dict[str, Any]:
        """获取群聊历史消息列表。

        Args:
            group_id: 号
            message_seq: 起始消息序号，0 表示从最新消息开始
            count: 获取消息数量，默认 20

        Returns:
            历史消息列表字典，包含消息数组等信息
        """
        return await self.get_group_msg_history_raw(
            {"group_id": group_id, "message_seq": message_seq, "count": count}
        )

    async def get_group_msg_history_raw(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """获取群聊历史消息（原始参数格式）。

        Args:
            payload: 原始请求载荷，包含 group_id、message_seq、count 等字段

        Returns:
            历史消息列表字典

        Note:
            待确认 NapCat 获取群历史消息参数后再收窄类型。
        """
        return await self.client.call_action("get_group_msg_history", payload)

    async def get_group_member_info(
        self,
        group_id: str,
        user_id: str,
        *,
        no_cache: bool = False,
    ) -> dict[str, Any]:
        """获取群成员详细信息。

        Args:
            group_id: 号
            user_id: 用户 ID
            no_cache: 是否禁用缓存，默认 False

        Returns:
            群成员信息字典，包含昵称、卡片名、权限等信息
        """
        payload = {"group_id": group_id, "user_id": user_id, "no_cache": no_cache}
        return await self.client.call_action("get_group_member_info", payload)

    async def get_group_member_list(self, group_id: str) -> dict[str, Any]:
        """获取群成员列表。

        Args:
            group_id: 号

        Returns:
            群成员列表字典，包含所有成员的信息数组
        """
        return await self.client.call_action(
            "get_group_member_list", {"group_id": group_id}
        )

    async def fetch_custom_face_detail(self, count: int = 48) -> dict[str, Any]:
        """获取机器人收藏的表情列表。

        Args:
            count: 获取表情数量，默认 48

        Returns:
            收藏表情列表字典，包含表情 ID、URL 等信息
        """
        return await self.client.call_action(
            "fetch_custom_face_detail", {"count": count}
        )

    async def send_msg(
        self,
        message_type: str,
        *,
        user_id: str = "",
        group_id: str = "",
        message: list[dict[str, Any]] | str,
    ) -> dict[str, Any]:
        """发送消息到指定目标。

        Args:
            message_type: 消息类型（"private" 或 "group"）
            user_id: 用户 ID（私聊时必填）
            group_id: 号（群聊时必填）
            message: 消息内容，可以是消息段列表或纯文本字符串

        Returns:
            发送结果字典，包含消息 ID 等信息
        """
        payload: dict[str, Any] = {
            "message_type": message_type,
            "message": _normalize_message(message),
        }
        if user_id:
            payload["user_id"] = user_id
        if group_id:
            payload["group_id"] = group_id
        return await self.client.call_action("send_msg", payload)

    async def send_private_msg(
        self, user_id: str, message: list[dict[str, Any]] | str
    ) -> dict[str, Any]:
        """发送私聊消息。

        Args:
            user_id: 用户 ID
            message: 消息内容，可以是消息段列表或纯文本字符串

        Returns:
            发送结果字典，包含消息 ID 等信息
        """
        return await self.send_msg("private", user_id=user_id, message=message)

    async def send_group_msg(
        self, group_id: str, message: list[dict[str, Any]] | str
    ) -> dict[str, Any]:
        """发送群聊消息。

        Args:
            group_id: 号
            message: 消息内容，可以是消息段列表或纯文本字符串

        Returns:
            发送结果字典，包含消息 ID 等信息
        """
        return await self.send_msg("group", group_id=group_id, message=message)


def _normalize_message(message: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    """规范化消息内容为消息段列表格式。

    Args:
        message: 消息内容，可以是消息段列表或纯文本字符串

    Returns:
        规范化后的消息段列表
    """
    if isinstance(message, str):
        return [{"type": "text", "data": {"text": message}}]
    return [dict(item) for item in message if isinstance(item, dict)]
