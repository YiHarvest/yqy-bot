"""YQY Bot 入口脚本。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from iamai import Runtime
from iamai.config import load_env_file

_PROJECT_ROOT = Path(__file__).resolve().parent


def main() -> None:
    load_env_file(_PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run the bot")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.terminal.toml")),
        help="Path to the TOML config file",
    )
    args = parser.parse_args()

    runtime = Runtime.from_config_file(args.config)
    asyncio.run(runtime.serve())


if __name__ == "__main__":
    main()
