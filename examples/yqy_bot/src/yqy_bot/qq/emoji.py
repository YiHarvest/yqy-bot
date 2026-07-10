from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from typing import Any

from yqy_bot.core.models import GeneratedResponse, IntentDecision
from .napcat_tools import NapCatTools
from yqy_bot.storage.repositories import Repositories

LOGGER = logging.getLogger(__name__)

BUILTIN_FACES = [
    {"face_id": "14", "summary": "微笑"},
    {"face_id": "1", "summary": "撇嘴"},
    {"face_id": "13", "summary": "呲牙"},
    {"face_id": "21", "summary": "尴尬"},
    {"face_id": "23", "summary": "大哭"},
    {"face_id": "76", "summary": "偷笑"},
    {"face_id": "118", "summary": "抱拳"},
]


@dataclass(slots=True)
class EmojiItem:
    emoji_type: str
    face_id: str = ""
    emoji_id: str = ""
    emoji_package_id: str = ""
    key: str = ""
    summary: str = ""
    image_url: str = ""
    raw_json: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """将表情项转换为 API 请求载荷格式。

        Returns:
            载荷字典，包含 emoji_type、face_id、emoji_id 等字段
        """
        return {
            "emoji_type": self.emoji_type,
            "face_id": self.face_id,
            "emoji_id": self.emoji_id,
            "emoji_package_id": self.emoji_package_id,
            "key": self.key,
            "summary": self.summary,
            "image_url": self.image_url,
            "raw_json": self.raw_json or {},
        }

    def to_response(self) -> GeneratedResponse:
        """将表情项转换为回复对象。

        Returns:
            GeneratedResponse 实例，设置了相应的表情字段
        """
        return GeneratedResponse(
            text="",
            send_face=self.emoji_type == "face",
            face_id=self.face_id,
            send_mface=self.emoji_type == "mface",
            mface={
                "emoji_id": self.emoji_id,
                "emoji_package_id": self.emoji_package_id,
                "key": self.key,
                "summary": self.summary,
            },
            send_image=self.emoji_type == "image",
            image_url=self.image_url,
        )


class EmojiService:
    """表情服务类，管理机器人收藏表情的获取、选择和使用。"""

    def __init__(
        self, repos: Repositories, napcat: NapCatTools, *, custom_face_count: int = 48
    ) -> None:
        """初始化表情服务。

        Args:
            repos: 数据仓库对象，用于缓存表情数据
            napcat: NapCat 工具对象，用于获取表情列表
            custom_face_count: 获取收藏表情数量，默认 48
        """
        self.repos = repos
        self.napcat = napcat
        self.custom_face_count = custom_face_count

    async def refresh_custom_faces(self, count: int = 48) -> list[EmojiItem]:
        """刷新机器人收藏表情列表并缓存到数据库。

        Args:
            count: 获取表情数量，默认 48

        Returns:
            解析后的表情项列表
        """
        try:
            response = await self.napcat.fetch_custom_face_detail(count)
        except Exception as exc:
            LOGGER.warning(
                "fetch custom faces failed, fallback to builtin face: %s", exc
            )
            return []
        items = _extract_items(response)
        parsed: list[EmojiItem] = []
        for item in items:
            parsed_item = _parse_custom_face_item(item)
            if parsed_item is None:
                LOGGER.debug("skip unparseable custom face item: %s", item)
                continue
            parsed.append(parsed_item)
            self.repos.add_emoji(
                emoji_type=parsed_item.emoji_type,
                face_id=parsed_item.face_id,
                emoji_id=parsed_item.emoji_id,
                emoji_package_id=parsed_item.emoji_package_id,
                key=parsed_item.key,
                summary=parsed_item.summary,
                image_url=parsed_item.image_url,
                raw_json=parsed_item.raw_json or {},
            )
        return parsed

    async def get_random_custom_face(self) -> EmojiItem | None:
        """获取随机收藏表情。

        优先从缓存获取，缓存为空时刷新表情列表。

        Returns:
            随机表情项，如果没有任何表情则返回 None
        """
        cached = self.repos.random_emoji()
        if cached is not None:
            item = _emoji_from_row(cached)
            self._bump_usage(item)
            return item
        refreshed = await self.refresh_custom_faces(self.custom_face_count)
        if refreshed:
            item = random.choice(refreshed)
            self._bump_usage(item)
            return item
        return None

    async def get_random_builtin_face(self) -> EmojiItem:
        """获取随机内置表情（QQ 基础表情）。

        Returns:
            随机内置表情项
        """
        item = random.choice(BUILTIN_FACES)
        return EmojiItem(
            emoji_type="face",
            face_id=str(item["face_id"]),
            summary=str(item["summary"]),
        )

    async def choose_for_reply(
        self, intent: IntentDecision, reply_text: str
    ) -> EmojiItem | None:
        """根据意图和回复内容选择合适的表情。

        Args:
            intent: 意图决策对象
            reply_text: 回复文本内容

        Returns:
            选中的表情项，如果不需要表情则返回 None
        """
        text = reply_text.strip()
        explicit = self.is_explicit_request(text)
        if explicit or intent.need_emoji:
            custom = await self.get_random_custom_face()
            if custom is not None:
                return custom
            return await self.get_random_builtin_face()
        if any(token in text for token in ("笑", "哈", "嘿", "嗯哼")):
            custom = await self.get_random_custom_face()
            if custom is not None:
                return custom
        return None

    async def resolve_response(
        self,
        parsed_text: str,
        intent: IntentDecision,
        response: GeneratedResponse,
    ) -> GeneratedResponse:
        """解析并完善回复中的表情字段。

        当回复需要表情但未指定具体表情时，自动选择合适的表情填充。

        Args:
            parsed_text: 原始消息文本
            intent: 意图决策对象
            response: 生成的回复对象

        Returns:
            完善后的回复对象，表情字段已填充
        """
        normalized = response.normalized()
        if self.is_explicit_request(parsed_text):
            emoji = await self.choose_for_reply(intent, parsed_text)
            if emoji is not None:
                return emoji.to_response().normalized()
            return (await self.get_random_builtin_face()).to_response().normalized()
        if normalized.send_mface and not normalized.mface:
            custom = await self.get_random_custom_face()
            if custom is not None:
                normalized.send_mface = True
                normalized.mface = custom.to_response().mface
                normalized.text = ""
                return normalized.normalized()
            builtin = await self.get_random_builtin_face()
            return builtin.to_response().normalized()
        if normalized.send_face and not normalized.face_id:
            builtin = await self.get_random_builtin_face()
            normalized.send_face = True
            normalized.face_id = builtin.face_id
            if not normalized.text:
                normalized.text = ""
            return normalized.normalized()
        if (
            intent.need_emoji
            and not normalized.text
            and not normalized.send_face
            and not normalized.send_mface
        ):
            custom = await self.get_random_custom_face()
            if custom is not None:
                return custom.to_response().normalized()
            return (await self.get_random_builtin_face()).to_response().normalized()
        return normalized

    def is_explicit_request(self, text: str) -> bool:
        """判断消息是否为显式的表情请求。

        Args:
            text: 消息文本内容

        Returns:
            如果包含表情请求关键词则返回 True
        """
        return _is_explicit_emoji_request(text)

    def _bump_usage(self, item: EmojiItem) -> None:
        """增加表情的使用计数。

        Args:
            item: 表情项对象
        """
        if item.emoji_type == "face":
            self.repos.increment_emoji_usage(
                emoji_type=item.emoji_type,
                face_id=item.face_id,
            )
            return
        if item.emoji_type == "mface":
            self.repos.increment_emoji_usage(
                emoji_type=item.emoji_type,
                emoji_id=item.emoji_id,
                emoji_package_id=item.emoji_package_id,
                key=item.key,
            )
            return
        if item.emoji_type == "image":
            self.repos.increment_emoji_usage(
                emoji_type=item.emoji_type,
                image_url=item.image_url,
            )


def _is_explicit_emoji_request(text: str) -> bool:
    """判断文本是否包含显式的表情请求关键词。

    Args:
        text: 消息文本内容

    Returns:
        如果包含表情请求关键词则返回 True
    """
    keywords = [
        "发个表情",
        "来个表情",
        "表情包",
        "发你收藏的表情",
        "来个收藏表情",
        "来个收藏的表情",
    ]
    return any(keyword in text for keyword in keywords)


def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从 API 响应中提取表情项列表。

    Args:
        payload: API 响应字典

    Returns:
        表情项字典列表
    """
    if isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    data = payload.get("data")
    candidates: list[Any] = []
    if isinstance(data, dict):
        for key in ("faces", "emoji", "items", "list", "data"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        for value in data.values():
            if isinstance(value, list):
                candidates.extend(value)
        if not candidates:
            candidates.extend(
                [value for value in data.values() if isinstance(value, dict)]
            )
    elif isinstance(data, list):
        candidates.extend(data)
    if not candidates:
        for value in payload.values():
            if isinstance(value, list):
                candidates.extend(value)
    return [item for item in candidates if isinstance(item, dict)]


def _parse_custom_face_item(item: dict[str, Any]) -> EmojiItem | None:
    """解析收藏表情项字典为 EmojiItem 对象。

    Args:
        item: 表情项字典

    Returns:
        解析后的 EmojiItem 对象，如果数据无效则返回 None
    """
    emoji_id = _first_str(item, ("emoji_id", "id", "face_id"))
    emoji_package_id = _first_str(
        item, ("emoji_package_id", "package_id", "packageId", "pkg_id")
    )
    key = _first_str(item, ("key", "emoji_key", "mkey"))
    summary = _first_str(item, ("summary", "name", "title", "desc"))
    image_url = _first_str(item, ("url", "image_url", "file", "path"))
    face_id = _first_str(item, ("face_id",))

    if not any([emoji_id, emoji_package_id, key, image_url, face_id]):
        return None

    emoji_type = "mface"
    if face_id and not any([emoji_id, emoji_package_id, key]):
        emoji_type = "face"
    elif image_url and not any([emoji_id, emoji_package_id, key]):
        emoji_type = "image"

    return EmojiItem(
        emoji_type=emoji_type,
        face_id=face_id,
        emoji_id=emoji_id,
        emoji_package_id=emoji_package_id,
        key=key,
        summary=summary,
        image_url=image_url,
        raw_json=dict(item),
    )


def _emoji_from_row(row: dict[str, Any]) -> EmojiItem:
    """从数据库行数据构建 EmojiItem 对象。

    Args:
        row: 数据库行字典

    Returns:
        EmojiItem 对象
    """
    return EmojiItem(
        emoji_type=str(row.get("emoji_type", "mface")),
        face_id=str(row.get("face_id", "")),
        emoji_id=str(row.get("emoji_id", "")),
        emoji_package_id=str(row.get("emoji_package_id", "")),
        key=str(row.get("key", "")),
        summary=str(row.get("summary", "")),
        image_url=str(row.get("image_url", "")),
        raw_json=_safe_json(row.get("raw_json", "{}")),
    )


def _first_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    """从字典中按优先顺序获取第一个非空字符串值。

    Args:
        payload: 字典对象
        keys: 键名元组，按优先级排序

    Returns:
        第一个非空字符串值，如果都为空则返回空字符串
    """
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _safe_json(raw: Any) -> dict[str, Any]:
    """安全解析 JSON 字符串为字典。

    Args:
        raw: JSON 字符串或字典对象

    Returns:
        解析后的字典，如果解析失败则返回空字典
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
