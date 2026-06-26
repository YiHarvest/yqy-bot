# observability.py - 可观测性

## 概述

`observability.py` 提供运行时**指标计数**和**结构化审计日志**，用于健康检查、运维监控和问题排查。

**文件位置**：[`python/iamai/observability.py`](../python/iamai/observability.py)

## 核心类

### `MetricSeries` — 单条指标序列

```python
@dataclass(frozen=True)
class MetricSeries:
    name: str                              # 指标名（如 onebot_http_requests_total）
    labels: tuple[tuple[str, str], ...]     # 标签键值对（如 adapter=onebot11）
    value: int                              # 计数值

    def formatted_name() -> str             # Prometheus 风格名称
    def to_dict() -> dict                   # JSON 导出
```

**输出示例**：
- `formatted_name()` → `onebot_http_requests_total{adapter=onebot11,outcome=ok}`
- `to_dict()` → `{"name": "onebot_http_requests_total", "labels": {"adapter": "onebot11", "outcome": "ok"}, "value": 42}`

### `RuntimeMetrics` — 计数器仓库

```python
class RuntimeMetrics:
    def increment(name, value=1, **labels)   # 递增带标签的计数器
    def snapshot() -> dict[str, int]         # 格式化快照
    def series() -> list[MetricSeries]       # 获取所有指标序列
```

**用法**：
```python
runtime.count_metric("onebot_http_requests_total", adapter="onebot11", outcome="ok")
# → 计数器 internal: {("onebot_http_requests_total", (("adapter", "onebot11"), ("outcome", "ok")))} += 1
```

### `AuditLogger` — 审计日志器

```python
class AuditLogger:
    def __init__(logger_name: str = "iamai.audit")
    def emit(action, *, outcome="ok", level="INFO", **fields)
```

**输出格式**（每条一个 JSON）：
```json
{
    "ts": "2026-06-10T03:33:25.073800+00:00",
    "action": "runtime.reload",
    "outcome": "ok",
    "plugins": 2,
    "target": "plugins"
}
```

**适用场景**：
- 运行时重载记录：`action=runtime.reload`
- HTTP 请求审计：`action=onebot.http_request`
- 插件管理操作：`action=plugin.enable`

## 在 Runtime 中的集成

```python
class Runtime:
    def __init__(self):
        self.metrics = RuntimeMetrics()       # 计数器
        self.audit_logger = AuditLogger()     # 审计日志

    def count_metric(self, name, value=1, **labels):
        self.metrics.increment(name, value=value, **labels)

    def audit(self, action, *, outcome="ok", level=INFO, **fields):
        self.audit_logger.emit(action, outcome=outcome, level=level, **fields)
```

## 指标示例

| 指标名称 | 触发场景 |
|---------|---------|
| `onebot_http_requests_total` | OneBot11 HTTP API 调用计数 |
| `runtime.events_total` | 事件分发计数 |
| `runtime.errors_total` | 错误计数 |
