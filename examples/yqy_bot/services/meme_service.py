"""表情包服务：优先从本地收藏库发图，收藏库为空时回退到外部 API。
所有配置从 config/memes.json 加载。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "memes.json"
_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "memes" / "cache"
_FAVORITES_DIR = Path(__file__).resolve().parents[1] / "data" / "memes" / "favorites"
_LOCAL_BASE = "http://127.0.0.1:8000/meme"

_FAVORITES_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def _ensure_cache_dir() -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


_cfg = _load()


class MemeService:
    """表情包服务：下载 → 缓存 → 返回本地 FastAPI URL。"""

    # ═══════════════════════════════════════════
    #  本地收藏表情库
    # ═══════════════════════════════════════════

    async def get_favorite_meme_url(
        self, user_id: str, emotion: str | None = None
    ) -> dict[str, Any] | None:
        """从本地收藏库取一张表情包。

        Args:
            user_id: 当前用户 ID
            emotion: 情绪标签（可 None）

        Returns:
            image:  {"url": "http://127.0.0.1:8000/meme/favorites/{filename}", "type": "image", "data": None}
            mface:  {"url": "", "type": "mface", "data": {mface_segment_data}}
            None:   收藏库为空
        """
        from .db import get_connection

        conn = get_connection()
        try:
            row = None
            # 1. 优先匹配同 emotion
            if emotion:
                row = conn.execute(
                    """SELECT id, file_path, source_url, meme_type FROM favorite_memes
                       WHERE user_id = ? AND emotion = ?
                       ORDER BY usage_count ASC, RANDOM()
                       LIMIT 1""",
                    (user_id, emotion),
                ).fetchone()

            # 2. 没有 emotion 匹配 → 随机取该用户任意一张
            if row is None:
                row = conn.execute(
                    """SELECT id, file_path, source_url, meme_type FROM favorite_memes
                       WHERE user_id = ?
                       ORDER BY usage_count ASC, RANDOM()
                       LIMIT 1""",
                    (user_id,),
                ).fetchone()

            if row is None:
                logger.debug(f"MemeService: user={user_id} 本地收藏库为空")
                return None

            fav_id = row[0]
            file_path = Path(row[1])
            source_url = row[2]
            meme_type = row[3] or "image"

            # ── mface 类型：直接返回 mface 段数据 ──
            if meme_type == "mface":
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE favorite_memes SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
                    (now, fav_id),
                )
                conn.commit()
                try:
                    mface_data = json.loads(source_url or "{}")
                except (json.JSONDecodeError, TypeError):
                    mface_data = {}
                logger.info(f"MemeService: 命中本地收藏(mface) id={fav_id} emotion={emotion}")
                return {"url": "", "type": "mface", "data": mface_data}

            # ── image 类型 ──
            if not file_path.is_file():
                logger.warning(f"MemeService: favorites 文件不存在 {file_path}，删除记录")
                conn.execute("DELETE FROM favorite_memes WHERE id = ?", (fav_id,))
                conn.commit()
                return None

            filename = file_path.name
            local_url = f"{_LOCAL_BASE}/favorites/{filename}"

            # 更新使用计数
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE favorite_memes SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
                (now, fav_id),
            )
            conn.commit()

            logger.info(f"MemeService: 命中本地收藏 id={fav_id} emotion={emotion} → {local_url}")
            return {"url": local_url, "type": "image", "data": None}
        finally:
            conn.close()

    async def save_favorite_meme(
        self,
        user_id: str,
        source: str,
        emotion: str = "default",
        tags: str = "",
        is_url: bool = False,
        meme_type: str = "image",
    ) -> bool:
        """保存一张表情包到本地收藏库。

        Args:
            user_id: 用户 ID
            source: URL / 本地文件路径 / mface JSON 数据
            emotion: 情绪标签
            tags: 逗号分隔的标签
            is_url: True 表示 source 是 URL，False 表示本地文件路径
            meme_type: "image" 或 "mface"

        Returns:
            是否保存成功
        """
        try:
            # ── mface 类型：直接存 mface 段数据到 source_url，不下载 ──
            if meme_type == "mface":
                from .db import get_connection

                conn = get_connection()
                try:
                    # 用 dummy file_path（DB 要求 NOT NULL）
                    conn.execute(
                        """INSERT INTO favorite_memes (user_id, file_path, source_file, source_url, emotion, tags, meme_type)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (user_id, "", None, source, emotion, tags, "mface"),
                    )
                    conn.commit()
                    logger.info(
                        f"MemeService: mface收藏保存成功 user={user_id} "
                        f"emotion={emotion}"
                    )
                    return True
                finally:
                    conn.close()

            # ── image 类型：保持原有下载逻辑 ──
            if is_url:
                filename, file_path = await self._download_to_favorites(source)
                if file_path is None:
                    return False
                source_url = source
                source_file = None
            else:
                src_path = Path(source)
                if not src_path.is_file():
                    logger.warning(f"MemeService: 本地源文件不存在 {source}")
                    return False
                file_hash = self._file_hash(str(src_path))
                ext = src_path.suffix.lower().lstrip(".") or "gif"
                if ext not in {"jpg", "jpeg", "png", "gif", "webp"}:
                    ext = "gif"
                filename = f"{file_hash}.{ext}"
                file_path = _FAVORITES_DIR / filename
                shutil.copy2(str(src_path), str(file_path))
                source_url = None
                source_file = str(src_path)

            # 写入数据库
            from .db import get_connection

            conn = get_connection()
            try:
                conn.execute(
                    """INSERT INTO favorite_memes (user_id, file_path, source_file, source_url, emotion, tags, meme_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, str(file_path), source_file, source_url, emotion, tags, "image"),
                )
                conn.commit()
                logger.info(
                    f"MemeService: 收藏表情保存成功 user={user_id} "
                    f"emotion={emotion} file={filename}"
                )
                return True
            finally:
                conn.close()
        except Exception:
            logger.exception("MemeService: 收藏表情保存失败")
            return False

    async def _download_to_favorites(self, url: str) -> tuple[str, Path | None]:
        """从 URL 下载图片到 favorites 目录。

        Returns:
            (filename, file_path) 或 (filename, None) 表示失败
        """
        _FAVORITES_DIR.mkdir(parents=True, exist_ok=True)
        file_hash = self._file_hash(url)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"MemeService: favorites下载失败 HTTP={resp.status_code}")
                    return "", None

                content = resp.content
                if len(content) > 10 * 1024 * 1024:  # 10MB 限制
                    logger.warning(f"MemeService: favorites图片过大 {len(content)} bytes")
                    return "", None

                # 推断扩展名
                ext = self._guess_ext(url, content)
                filename = f"{file_hash}.{ext}"
                file_path = _FAVORITES_DIR / filename
                file_path.write_bytes(content)
                logger.info(f"MemeService: favorites下载成功 size={len(content)} file={filename}")
                return filename, file_path
        except Exception as exc:
            logger.warning(f"MemeService: favorites下载异常 url={url[:60]} error={exc}")
            return "", None

    @staticmethod
    def _file_hash(source: str) -> str:
        return hashlib.md5(source.encode()).hexdigest()

    @staticmethod
    def _guess_ext(url: str, content: bytes) -> str:
        """根据 URL 后缀或文件头推断扩展名。"""
        # 先看 URL
        path_part = url.split("?")[0]
        last_seg = path_part.rstrip("/").rsplit("/", 1)[-1]
        if "." in last_seg:
            raw_ext = last_seg.rsplit(".", 1)[-1].lower()
            if raw_ext in {"jpg", "jpeg", "png", "gif", "webp"}:
                return raw_ext
        # 再看 magic bytes
        if content[:4] == b"\x89PNG":
            return "png"
        if content[:3] == b"GIF":
            return "gif"
        if content[:2] == b"\xff\xd8":
            return "jpg"
        if content[:4] in (b"RIFF",) and content[8:12] == b"WEBP":
            return "webp"
        return "gif"

    # ═══════════════════════════════════════════
    #  外部 API（回退方案）
    # ═══════════════════════════════════════════

    async def get_meme_url(self, emotion: str, user_id: str | None = None) -> dict[str, Any] | None:
        """获取表情包 URL：优先本地收藏库，否则回退到外部 API。

        Args:
            emotion: happy / teasing / comfort / angry / excited
            user_id: 当前用户 ID（用于查本地收藏库）

        Returns:
            image:  {"url": "http://127.0.0.1:8000/meme/{filename}", "type": "image", "data": None}
            mface:  {"url": "", "type": "mface", "data": {mface_segment_data}}
            None:   获取失败
        """
        # ── 优先本地收藏库 ──
        if user_id:
            fav = await self.get_favorite_meme_url(user_id, emotion)
            if fav:
                return fav

        # ── 回退到外部 API ──
        category = _cfg.get(emotion)
        if not category:
            logger.warning(f"MemeService: 未知情绪分类 '{emotion}'，回退到 teasing")
            category = _cfg.get("teasing", {})

        api_url = category.get("api_url", "")
        if not api_url:
            return None

        remote_url = await self._fetch_remote_url(api_url)
        if not remote_url:
            return None

        logger.info(f"MemeService: emotion={emotion} 远程URL={remote_url[:80]}")

        filename = self._cache_filename(remote_url)
        filepath = _CACHE_DIR / filename

        if filepath.is_file():
            local_url = f"{_LOCAL_BASE}/{filename}"
            logger.info(f"MemeService: 命中缓存 → {local_url}")
            return {"url": local_url, "type": "image", "data": None}

        success = await self._download_image(remote_url, filepath)
        if not success:
            return None

        local_url = f"{_LOCAL_BASE}/{filename}"
        logger.info(f"MemeService: 下载成功 → {local_url}")
        return {"url": local_url, "type": "image", "data": None}

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

    async def _download_image(self, remote_url: str, filepath: Path) -> bool:
        """下载远程图片到本地文件。"""
        _ensure_cache_dir()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
            ) as client:
                resp = await client.get(remote_url)
                if resp.status_code == 200:
                    filepath.write_bytes(resp.content)
                    logger.info(
                        f"MemeService: 下载成功 "
                        f"size={len(resp.content)}bytes "
                        f"file={filepath.name}"
                    )
                    return True
                else:
                    logger.warning(
                        f"MemeService: 下载失败 HTTP={resp.status_code} url={remote_url[:60]}"
                    )
        except Exception as exc:
            logger.warning(f"MemeService: 下载失败 url={remote_url[:60]} error={exc}")
        return False

    @staticmethod
    def _cache_filename(url: str) -> str:
        """根据 URL 生成缓存文件名：md5(url) + 原始扩展名。"""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        ext = "gif"
        path_part = url.split("?")[0]
        last_seg = path_part.rstrip("/").rsplit("/", 1)[-1]
        if "." in last_seg:
            raw_ext = last_seg.rsplit(".", 1)[-1].lower()
            if raw_ext in {"jpg", "jpeg", "png", "gif", "webp"}:
                ext = raw_ext
        return f"{url_hash}.{ext}"

    @staticmethod
    def classify_emotion(mood_state: dict[str, int]) -> str:
        """根据情绪状态返回合适的分类标签。"""
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
