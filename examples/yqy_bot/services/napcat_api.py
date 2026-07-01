"""NapCat API 封装层。

统一包装 OneBot 适配器上的常用动作，避免业务代码直接散落 send_message/call_api。
"""

from __future__ import annotations

from typing import Any


class NapCatAPI:
    """面向 NapCat 的轻量封装。"""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    @classmethod
    def from_adapter(cls, adapter: Any) -> "NapCatAPI":
        return cls(adapter)

    @staticmethod
    def _normalize_target(target: Any) -> dict[str, str]:
        if isinstance(target, dict):
            if "group_id" in target:
                return {"group_id": str(target.get("group_id", ""))}
            if "user_id" in target:
                return {"user_id": str(target.get("user_id", ""))}
        if target is None:
            return {}
        return {"user_id": str(target)}

    async def send_safe_message(self, target: Any, message: Any) -> Any:
        """统一发送消息入口。

        优先调用 NapCat / OneBot 的 `send_msg`，并按目标自动填充
        `message_type`、`user_id`、`group_id` 和 `message`。
        """
        normalized = self._normalize_target(target)
        if "group_id" in normalized:
            group_id = normalized["group_id"]
            try:
                return await self._adapter.call_api(
                    "send_msg",
                    message_type="group",
                    group_id=group_id,
                    message=message,
                )
            except Exception:
                return await self._adapter.send_message(
                    message, target={"group_id": group_id}
                )

        user_id = normalized.get("user_id", "")
        try:
            return await self._adapter.call_api(
                "send_msg",
                message_type="private",
                user_id=user_id,
                message=message,
            )
        except Exception:
            return await self._adapter.send_message(
                message, target={"user_id": user_id}
            )

    async def send_msg(self, user_id: str, message: Any) -> Any:
        """发送私聊消息。"""
        return await self.send_safe_message({"user_id": str(user_id)}, message)

    async def send_private_msg(self, user_id: str, message: Any) -> Any:
        """发送私聊消息。"""
        return await self.send_safe_message({"user_id": str(user_id)}, message)

    async def send_group_msg(self, group_id: str, message: Any) -> Any:
        """发送群聊消息。"""
        return await self.send_safe_message({"group_id": str(group_id)}, message)

    async def send_poke(self, user_id: str) -> Any:
        """发送戳一戳。"""
        return await self._adapter.call_api("send_poke", user_id=str(user_id))

    async def fetch_custom_face(self, count: int = 48) -> Any:
        """获取收藏表情列表。"""
        return await self._adapter.call_api("fetch_custom_face", count=count)

    async def fetch_custom_face_detail(self, count: int = 48) -> Any:
        """获取收藏表情详情。"""
        return await self._adapter.call_api("fetch_custom_face_detail", count=count)

    async def add_custom_face(self, file_path: str, is_origin: bool = True) -> Any:
        """添加收藏表情。"""
        return await self._adapter.call_api(
            "add_custom_face",
            file=file_path,
            is_origin=is_origin,
        )

    async def delete_custom_face(self, res_id: str | list[str]) -> Any:
        """删除收藏表情。"""
        return await self._adapter.call_api("delete_custom_face", res_id=res_id)

    @property
    def adapter(self) -> Any:
        """返回底层适配器，便于少量兼容逻辑复用。"""
        return self._adapter

    async def call_api(self, action: str, **params: Any) -> Any:
        """通用 API 调用。"""
        return await self._adapter.call_api(action, **params)
