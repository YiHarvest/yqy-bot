"""iamai 运行时的指标计数器和结构化审计日志。"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger


@dataclass(frozen=True, slots=True)
class MetricSeries:
    """一条带标签和值的计数器指标序列。"""

    name: str
    labels: tuple[tuple[str, str], ...]
    value: int

    def formatted_name(self) -> str:
        """返回按 Prometheus 风格格式化的指标名称（含标签）。"""
        if not self.labels:
            return self.name
        label_text = ",".join(f"{key}={value}" for key, value in self.labels)
        return f"{self.name}{{{label_text}}}"

    def to_dict(self) -> dict[str, Any]:
        """将此指标序列转为 JSON 兼容的字典。"""
        return {
            "name": self.name,
            "labels": dict(self.labels),
            "value": self.value,
        }


class RuntimeMetrics:
    """轻量级内存计数器仓库，用于健康检查和运维巡检。"""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)

    def increment(self, name: str, value: int = 1, **labels: Any) -> None:
        """递增一个带标签的计数器。"""
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("metric name cannot be empty")
        label_items = tuple(sorted((str(key), str(item)) for key, item in labels.items()))
        self._counters[(normalized_name, label_items)] += int(value)

    def snapshot(self) -> dict[str, int]:
        """Return counters keyed by their formatted metric names."""
        return {
            MetricSeries(name=name, labels=labels, value=value).formatted_name(): value
            for (name, labels), value in sorted(self._counters.items())
        }

    def series(self) -> list[MetricSeries]:
        """Return all metric series sorted for deterministic output."""
        return [
            MetricSeries(name=name, labels=labels, value=value)
            for (name, labels), value in sorted(self._counters.items())
        ]


class AuditLogger:
    """结构化审计日志器，每个运行时事件输出一条 JSON 记录。"""

    def __init__(self, logger_name: str = "iamai.audit") -> None:
        self.logger_name = logger_name
        self._logger = logger.bind(name=logger_name, audit=True)

    def emit(
        self,
        action: str,
        *,
        outcome: str = "ok",
        level: int | str = "INFO",
        **fields: Any,
    ) -> None:
        """发送一条结构化审计记录。"""
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": str(action),
            "outcome": str(outcome),
        }
        for key, value in fields.items():
            if value is None:
                continue
            payload[str(key)] = value
        self._logger.log(level, json.dumps(payload, ensure_ascii=False, sort_keys=True))
