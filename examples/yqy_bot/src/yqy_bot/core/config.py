from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import BotSettings, PersonaSettings, SafetySettings


@dataclass(slots=True)
class ProjectConfig:
    root: Path
    bot: BotSettings
    persona: PersonaSettings
    safety: SafetySettings

    @property
    def config_dir(self) -> Path:
        """获取配置文件目录路径。

        Returns:
            配置目录的 Path 对象，位于项目根目录下的 config 子目录
        """
        return self.root / "config"

    @property
    def database_path(self) -> Path:
        """获取数据库文件路径。

        Returns:
            数据库文件的 Path 对象，路径已解析为绝对路径
        """
        return (self.root / self.bot.database_path).resolve()


def load_project_config(root: Path) -> ProjectConfig:
    """从项目根目录加载完整的配置对象。

    Args:
        root: 项目根目录路径

    Returns:
        包含机器人、人设和安全设置的完整配置对象

    Raises:
        FileNotFoundError: 当配置文件不存在时抛出
    """
    config_dir = root / "config"
    bot = BotSettings.from_mapping(_load_json(config_dir / "bot.json"))
    persona = PersonaSettings.from_mapping(_load_json(config_dir / "persona.json"))
    safety = SafetySettings.from_mapping(_load_json(config_dir / "safety.json"))
    return ProjectConfig(root=root, bot=bot, persona=persona, safety=safety)


def load_json_file(path: Path) -> dict[str, Any]:
    """加载 JSON 文件并返回字典对象。

    Args:
        path: JSON 文件路径

    Returns:
        解析后的字典对象

    Raises:
        FileNotFoundError: 当文件不存在时抛出
        ValueError: 当 JSON 内容不是对象类型时抛出
    """
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _load_json(path: Path) -> Any:
    """读取 JSON 文件并解析内容。

    Args:
        path: JSON 文件路径

    Returns:
        解析后的 JSON 数据（可以是任意类型）

    Raises:
        FileNotFoundError: 当文件不存在时抛出
    """
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
