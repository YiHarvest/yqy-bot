"""记忆提取过滤规则，从 config/memory_filter.json 加载。"""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "memory_filter.json"


def _load() -> dict:
    if _CONFIG_PATH.is_file():
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


_cfg = _load()

SKIP_KEYWORDS: frozenset[str] = frozenset(_cfg.get("skip_keywords", []))
MIN_TEXT_LENGTH: int = int(_cfg.get("min_text_length", 8))
QUESTION_MARKS: frozenset[str] = frozenset(_cfg.get("question_marks", []))
