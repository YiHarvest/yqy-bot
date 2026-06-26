from __future__ import annotations

import argparse
import asyncio
import threading
from pathlib import Path

import uvicorn
from iamai import Runtime

_PROJECT_ROOT = Path(__file__).resolve().parent


def _start_meme_server() -> None:
    """后台线程启动 FastAPI 图片缓存服务。"""
    try:
        uvicorn.run(
            "services.meme_server:app",
            host="127.0.0.1",
            port=8000,
            log_level="info",
        )
    except Exception:
        import traceback
        traceback.print_exc()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the yqy_bot")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.terminal.toml")),
        help="Path to the TOML config file",
    )
    parser.add_argument(
        "--no-meme-server",
        action="store_true",
        help="不启动 FastAPI 图片缓存服务",
    )
    args = parser.parse_args()

    # 后台启动 FastAPI 图片缓存服务
    if not args.no_meme_server:
        t = threading.Thread(target=_start_meme_server, daemon=True)
        t.start()
        print("FastAPI meme server starting on http://127.0.0.1:8000 ...")

    runtime = Runtime.from_config_file(args.config)
    asyncio.run(runtime.serve())


if __name__ == "__main__":
    main()
