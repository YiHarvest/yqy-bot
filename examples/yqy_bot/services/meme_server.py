"""本地 FastAPI 图片缓存服务。
在 http://127.0.0.1:8000/meme/{filename} 提供已缓存的斗图图片。
在 http://127.0.0.1:8000/meme/favorites/{filename} 提供本地收藏表情包。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "memes" / "cache"
FAVORITES_DIR = Path(__file__).resolve().parents[1] / "data" / "memes" / "favorites"

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"jpg", "jpeg", "png", "gif", "webp"})

app = FastAPI(title="Meme Cache Server", version="1.0.0")


def _validate_filename(filename: str) -> str:
    """校验文件名，防路径穿越，返回小写扩展名。"""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")
    return ext


@app.get("/meme/{filename}")
async def serve_meme(filename: str, t: str = Query(default="", description="缓存破坏参数，忽略")):
    """返回本地缓存的图片文件（外部 API 缓存）。"""
    ext = _validate_filename(filename)
    file_path = CACHE_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="图片未找到")
    media_type = mimetypes.types_map.get(f".{ext}", "application/octet-stream")
    return FileResponse(path=str(file_path), media_type=media_type, filename=filename)


@app.get("/meme/favorites/{filename}")
async def serve_favorite_meme(filename: str, t: str = Query(default="", description="缓存破坏参数，忽略")):
    """返回本地收藏表情包（data/memes/favorites/）。"""
    ext = _validate_filename(filename)
    file_path = FAVORITES_DIR / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="收藏表情未找到")
    media_type = mimetypes.types_map.get(f".{ext}", "application/octet-stream")
    return FileResponse(path=str(file_path), media_type=media_type, filename=filename)


@app.get("/health")
async def health():
    """健康检查接口。"""
    return {"status": "ok", "cache_dir": str(CACHE_DIR), "cached_files": _count_files()}


def _count_files() -> int:
    try:
        return sum(1 for _ in CACHE_DIR.iterdir() if _.is_file())
    except Exception:
        return 0
