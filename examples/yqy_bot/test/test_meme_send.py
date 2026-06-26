"""测试脚本：确认 FastAPI 图片服务 + OneBot11 image 发送全链路正常。

用法：
    python test/test_meme_send.py

前提：
    - FastAPI meme_server 已在 http://127.0.0.1:8000 运行
    - 或先启动:  uvicorn services.meme_server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

# 把项目根加入到 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

import httpx
from loguru import logger

CACHE_DIR = _PROJECT_ROOT / "data" / "memes" / "cache"
TEST_FILE = "test.gif"
TEST_URL = f"http://127.0.0.1:8000/meme/{TEST_FILE}"

# 最小合法 GIF（1x1 透明像素，43 bytes）
_MINIMAL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw=="
)


def _ensure_test_image() -> None:
    """在缓存目录创建测试用的 1x1 GIF。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / TEST_FILE).write_bytes(_MINIMAL_GIF)


async def test_fastapi_health() -> bool:
    """测试 FastAPI 健康检查接口。"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get("http://127.0.0.1:8000/health")
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"FastAPI 健康检查 OK: cached_files={data.get('cached_files')}")
                return True
            logger.error(f"FastAPI 健康检查失败 HTTP={resp.status_code}")
    except Exception as exc:
        logger.error(f"FastAPI 健康检查异常: {exc}")
    return False


async def test_serve_local_image() -> bool:
    """FastAPI 能否正常返回本地图片。"""
    _ensure_test_image()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(TEST_URL)
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                logger.info(
                    f"FastAPI 图片返回 OK "
                    f"size={len(resp.content)}bytes "
                    f"content_type={content_type}"
                )
                return True
            logger.error(f"FastAPI 图片返回失败 HTTP={resp.status_code}")
    except Exception as exc:
        logger.error(f"FastAPI 图片拉取异常: {exc}")
    return False


async def test_meme_url_format() -> bool:
    """验证 MemeService 返回的本地 URL 格式正确。"""
    from services import meme_service

    # 用 _cache_filename 验证文件名生成逻辑
    test_remote = "https://example.com/img/test.gif"
    filename = meme_service.MemeService._cache_filename(test_remote)
    local_url = f"{meme_service._LOCAL_BASE}/{filename}"

    if local_url.startswith("http://127.0.0.1:8000/meme/"):
        logger.info(f"MemeService URL 格式正确: {local_url}")
        return True
    logger.error(f"MemeService URL 格式错误: {local_url}")
    return False


async def test_onebot_image_segment() -> bool:
    """验证 OneBot11 image 消息段格式。"""
    from services.human_behavior import (
        _face_segment,
        _image_segment,
        _reply_segment,
        _text_segment,
    )

    # image 段
    img = _image_segment(TEST_URL)
    assert img == {"type": "image", "data": {"file": TEST_URL}}, f"image段错误: {img}"
    # face 段
    face = _face_segment("14")
    assert face == {"type": "face", "data": {"id": "14"}}, f"face段错误: {face}"
    # text 段
    txt = _text_segment("hello")
    assert txt == {"type": "text", "data": {"text": "hello"}}, f"text段错误: {txt}"
    # reply 段
    reply = _reply_segment(123)
    assert reply == {"type": "reply", "data": {"id": "123"}}, f"reply段错误: {reply}"

    logger.info("OneBot11 消息段格式全部正确")
    return True


async def test_path_traversal_blocked() -> bool:
    """验证路径穿越攻击被拦截。"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get("http://127.0.0.1:8000/meme/../secret.txt")
            if resp.status_code in (400, 404):
                logger.info(f"路径穿越防护 OK HTTP={resp.status_code}")
                return True
            logger.error(f"路径穿越未拦截! HTTP={resp.status_code}")
    except Exception as exc:
        logger.error(f"路径穿越测试异常: {exc}")
    return False


async def main() -> None:
    print("=" * 50)
    print("  图片缓存服务测试")
    print("=" * 50)

    results: dict[str, bool] = {}

    print("\n[1/6] FastAPI 健康检查...")
    results["health"] = await test_fastapi_health()

    print("\n[2/6] FastAPI 本地图片返回...")
    results["serve"] = await test_serve_local_image()

    print("\n[3/6] MemeService URL 格式...")
    results["url_format"] = await test_meme_url_format()

    print("\n[4/6] OneBot11 消息段格式...")
    results["segment"] = await test_onebot_image_segment()

    print("\n[5/6] 路径穿越防护...")
    results["security"] = await test_path_traversal_blocked()

    print("\n[6/6] 斗图 API 联通性...")
    results["remote_api"] = await _test_remote_api()

    # 汇总
    print("\n" + "=" * 50)
    print("  测试结果")
    print("=" * 50)
    for name, ok in results.items():
        status = "OK" if ok else "SKIP" if name == "remote_api" else "FAIL"
        print(f"  [{status}] {name}")
    core_ok = all(v for k, v in results.items() if k != "remote_api")
    print(f"\n核心功能: {'全部通过!' if core_ok else '部分失败，请检查日志'}")
    if not results.get("remote_api"):
        print("斗图 API 不可用（外部依赖，不影响核心功能）")

    if not results.get("health"):
        print("\n提示: 请先启动 FastAPI 图片服务:")
        print("  uvicorn services.meme_server:app --host 127.0.0.1 --port 8000")


async def _test_remote_api() -> bool:
    """斗图 API 联通性测试（可选），不阻塞核心流程。"""
    try:
        from services.meme_service import MemeService
        svc = MemeService()
        url = await svc.get_meme_url("happy")
        if url:
            logger.info(f"斗图 API 可用: {url}")
            return True
        logger.warning("斗图 API 不可用（外部依赖）")
    except Exception as exc:
        logger.warning(f"斗图 API 测试异常: {exc}")
    return False


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
