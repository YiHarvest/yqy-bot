"""Rust 核心扩展 (PyO3) 的 Python 薄封装层。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from ._core import CoreMessage, deep_merge_json, next_event_id, normalize_onebot11_event


def merge_dicts(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """深度合并两个 JSON 兼容字典。overlay 的值覆盖 base 的同名键。"""
    return cast(
        dict[str, Any],
        json.loads(deep_merge_json(json.dumps(base), json.dumps(overlay))),
    )


def new_event_id() -> str:
    """返回 Rust 核心生成的全局唯一事件 ID。"""
    return next_event_id()


def normalize_onebot11_payload(
    raw: Mapping[str, Any], *, adapter_name: str = "onebot11", platform: str = "qq"
) -> dict[str, Any]:
    """将原始 OneBot11 事件标准化为 iamai 的统一事件格式。"""
    return cast(
        dict[str, Any],
        json.loads(normalize_onebot11_event(json.dumps(raw), adapter_name, platform)),
    )


__all__ = ["CoreMessage", "merge_dicts", "new_event_id", "normalize_onebot11_payload"]
