"""表情包服务：使用 NapCat API 管理 QQ 客户端本地表情库。

核心改动：
- 存储：调用 /add_custom_face（需要本地文件路径）
- 查询：调用 /fetch_custom_face 或 /fetch_custom_face_detail
- 发送：直接用 URL 作为 image 类型
- 删除：调用 /delete_custom_face
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any, TYPE_CHECKING

import httpx
from loguru import logger

from .config_service import get_memes_config, DATA_DIR

if TYPE_CHECKING:
    from iamai.adapter import Adapter

# 本地缓存目录（用于下载后添加到 QQ）
_DOWNLOAD_DIR = DATA_DIR / "memes" / "download"
_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 外部 API 配置（回退方案）
_cfg = get_memes_config()


class MemeService:
    """表情包服务：优先 NapCat API，回退到外部斗图 API。"""

    # ═══════════════════════════════════════════
    #  NapCat API：QQ 客户端本地表情库
    # ═══════════════════════════════════════════

    async def get_qq_favorites(self, adapter: "Adapter", count: int = 48) -> list[str]:
        """获取 QQ 客户端收藏表情 URL 列表。"""
        try:
            result = await adapter.call_api("fetch_custom_face", count=count)
            if result.get("status") == "ok":
                urls = result.get("data", [])
                logger.info(f"MemeService: QQ收藏表情获取成功 count={len(urls)}")
                return urls
            logger.warning(f"MemeService: QQ收藏表情获取失败 {result}")
            return []
        except Exception as exc:
            logger.warning(f"MemeService: fetch_custom_face 异常 error={exc}")
            return []

    async def get_qq_favorites_detail(
        self, adapter: "Adapter", count: int = 48
    ) -> list[dict[str, Any]]:
        """获取 QQ 客户端收藏表情详细信息（含 resId）。"""
        try:
            result = await adapter.call_api("fetch_custom_face_detail", count=count)
            if result.get("status") == "ok":
                data = result.get("data", {})
                if isinstance(data, dict):
                    records = data.get("emojiRecords") or data.get("records") or []
                elif isinstance(data, list):
                    records = data
                else:
                    records = []
                logger.info(f"MemeService: QQ收藏表情详情获取成功 count={len(records)}")
                return records
            logger.warning(f"MemeService: fetch_custom_face_detail 失败 {result}")
            return []
        except Exception as exc:
            logger.warning(f"MemeService: fetch_custom_face_detail 异常 error={exc}")
            return []

    async def add_to_qq(
        self, adapter: "Adapter", file_path: str | Path, is_origin: bool = True
    ) -> bool:
        """将表情添加到 QQ 客户端本地表情库。"""
        fp = Path(file_path)
        if not fp.is_file():
            logger.warning(f"MemeService: 文件不存在 {fp}")
            return False

        try:
            result = await adapter.call_api(
                "add_custom_face",
                file=str(fp),
                is_origin=is_origin,
            )
            if result.get("status") == "ok":
                logger.info(f"MemeService: QQ本地表情添加成功 file={fp}")
                return True
            logger.warning(f"MemeService: add_custom_face 失败 {result}")
            return False
        except Exception as exc:
            logger.warning(f"MemeService: add_custom_face 异常 error={exc}")
            return False

    async def delete_from_qq(self, adapter: "Adapter", res_id: str | list[str]) -> bool:
        """从 QQ 客户端删除收藏表情。"""
        try:
            result = await adapter.call_api("delete_custom_face", res_id=res_id)
            if result.get("status") == "ok":
                logger.info(f"MemeService: QQ本地表情删除成功 res_id={res_id}")
                return True
            logger.warning(f"MemeService: delete_custom_face 失败 {result}")
            return False
        except Exception as exc:
            logger.warning(f"MemeService: delete_custom_face 异常 error={exc}")
            return False

    # ═══════════════════════════════════════════
    #  发送表情：优先 QQ 收藏，回退外部 API
    # ═══════════════════════════════════════════

    async def get_meme_url(
        self,
        adapter: "Adapter",
        emotion: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """获取表情包 URL：优先 QQ 收藏库，否则回退外部 API。"""
        # ── 优先 QQ 收藏表情 ──
        urls = await self.get_qq_favorites(adapter, count=48)
        if urls:
            url = random.choice(urls)
            logger.info(f"MemeService: 命中QQ收藏 emotion={emotion} → {url[:60]}")
            return {"url": url, "type": "image", "data": None}

        # ── 回退外部斗图 API ──
        category = _cfg.get(emotion or "teasing", {})
        api_url = category.get("api_url", "")
        if api_url:
            remote_url = await self._fetch_remote_url(api_url)
            if remote_url:
                logger.info(
                    f"MemeService: 回退外部API emotion={emotion} → {remote_url[:60]}"
                )
                return {"url": remote_url, "type": "image", "data": None}

        logger.warning(f"MemeService: 无表情可用 emotion={emotion}")
        return None

    # ═══════════════════════════════════════════
    #  保存表情：下载 → 添加到 QQ
    # ═══════════════════════════════════════════

    async def save_from_url(self, adapter: "Adapter", url: str) -> bool:
        """从 URL 下载图片并添加到 QQ 本地表情库。"""
        file_path = await self._download_to_temp(url)
        if file_path is None:
            return False

        success = await self.add_to_qq(adapter, file_path)

        # 清理临时文件
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass

        return success

    async def save_from_local(self, adapter: "Adapter", file_path: str | Path) -> bool:
        """从本地文件添加到 QQ 本地表情库。"""
        return await self.add_to_qq(adapter, file_path)

    async def save_from_mface(
        self, adapter: "Adapter", mface_data: dict[str, Any]
    ) -> bool:
        """从 mface 数据添加到 QQ 本地表情库。"""
        url = mface_data.get("url") or mface_data.get("emoji_url") or ""
        if not url:
            logger.warning("MemeService: mface 缺少 url，无法保存到 QQ")
            return False
        return await self.save_from_url(adapter, url)

    # ═══════════════════════════════════════════
    #  辅助方法
    # ═══════════════════════════════════════════

    async def _download_to_temp(self, url: str) -> Path | None:
        """从 URL 下载图片到临时目录。"""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"MemeService: 下载失败 HTTP={resp.status_code}")
                    return None

                content = resp.content
                if len(content) > 10 * 1024 * 1024:
                    logger.warning(f"MemeService: 图片过大 {len(content)} bytes")
                    return None

                ext = self._guess_ext(url, content)
                file_hash = hashlib.md5(url.encode()).hexdigest()
                filename = f"{file_hash}.{ext}"
                file_path = _DOWNLOAD_DIR / filename

                file_path.write_bytes(content)
                logger.info(f"MemeService: 下载成功 size={len(content)} → {file_path}")
                return file_path
        except Exception as exc:
            logger.warning(f"MemeService: 下载异常 url={url[:60]} error={exc}")
            return None

    async def _fetch_remote_url(self, api_url: str) -> str | None:
        """从斗图 API 拉取远程图片 URL。"""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    url = data.get("url") or data.get("imgurl")
                    if url:
                        return str(url)
        except Exception as exc:
            logger.warning(f"MemeService: API请求失败 api={api_url} error={exc}")
        return None

    @staticmethod
    def _guess_ext(url: str, content: bytes) -> str:
        """根据 URL 或文件头推断扩展名。"""
        path_part = url.split("?")[0]
        last_seg = path_part.rstrip("/").rsplit("/", 1)[-1]
        if "." in last_seg:
            raw_ext = last_seg.rsplit(".", 1)[-1].lower()
            if raw_ext in {"jpg", "jpeg", "png", "gif", "webp"}:
                return raw_ext
        if content[:4] == b"\x89PNG":
            return "png"
        if content[:3] == b"GIF":
            return "gif"
        if content[:2] == b"\xff\xd8":
            return "jpg"
        if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "webp"
        return "gif"

    @staticmethod
    def classify_emotion(mood_state: dict[str, int]) -> str:
        """根据情绪状态返回分类标签。"""
        mood = mood_state.get("mood", 50)
        energy = mood_state.get("energy", 50)
        if mood > 80:
            return "excited"
        if mood > 65:
            return "happy"
        if mood < 25:
            return "angry"
        if energy < 25:
            return "angry"
        return "teasing"

    # ═══════════════════════════════════════════
    #  兼容旧接口（逐步废弃）
    # ═══════════════════════════════════════════

    async def get_favorite_meme_url(
        self, user_id: str, emotion: str | None = None
    ) -> dict[str, Any] | None:
        """兼容旧接口：返回 None（已废弃本地收藏库）。"""
        logger.warning("MemeService: get_favorite_meme_url 已废弃，请使用 get_meme_url")
        return None

    async def save_favorite_meme(
        self,
        user_id: str,
        source: str,
        emotion: str = "default",
        tags: str = "",
        is_url: bool = False,
        meme_type: str = "image",
    ) -> bool:
        """兼容旧接口：已废弃本地数据库存储。"""
        logger.warning("MemeService: save_favorite_meme 已废弃本地数据库存储")
        return False

    def convert_mface_to_image(
        self, mface_data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """将 mface 数据转换为 image 类型。"""
        url = mface_data.get("url") or mface_data.get("emoji_url") or ""
        if url:
            return {"url": url, "type": "image", "data": None}
        return None
