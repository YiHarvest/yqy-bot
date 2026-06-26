# core.py - Rust 核心 Python 封装

## 概述

`core.py` 是对 Rust 侧（PyO3）`_core` 原生扩展的**薄封装层**，将 Rust 实现的高性能函数暴露为 Python API。

**文件位置**：[`python/iamai/core.py`](../python/iamai/core.py)

## 底层 Rust 类：`CoreMessage`（导出自 `_core`）

`CoreMessage` 是消息链的底层 Rust 实现，提供高性能的消息段操作。

| 方法 | 说明 |
|------|------|
| `from_plain_text(text)` | 从纯文本创建消息 |
| `from_json(payload)` | 从 JSON 创建消息 |
| `from_onebot11_json(payload)` | 从 OneBot11 格式 JSON 创建消息 |
| `push_text(text)` | 追加文本段 |
| `push(kind, data_json)` | 追加通用段（image/face/at 等） |
| `plain_text()` | 提取纯文本内容 |
| `render_text()` | 调试友好的文本渲染 |
| `to_json()` | 序列化为 JSON |
| `to_onebot11_json()` | 序列化为 OneBot11 格式 JSON |
| `copy()` | 深拷贝 |

## Python 封装函数

### `merge_dicts(base, overlay)`

```python
def merge_dicts(
    base: Mapping[str, Any], 
    overlay: Mapping[str, Any]
) -> dict[str, Any]
```

**功能**：深度合并两个 JSON 字典，`overlay` 的值覆盖 `base` 的同名键。

**实现**：调用 Rust 的 `deep_merge_json`，比纯 Python 实现更快。

### `new_event_id()`

```python
def new_event_id() -> str
```

**功能**：生成全局唯一的事件 ID（由 Rust 核心实现）。

### `normalize_onebot11_payload(raw, *, adapter_name, platform)`

```python
def normalize_onebot11_payload(
    raw: Mapping[str, Any],
    *,
    adapter_name: str = "onebot11",
    platform: str = "qq"
) -> dict[str, Any]
```

**功能**：将 OneBot11 协议的原始 payload 标准化为 iamai 的内部事件格式。

**调用链**：
```
OneBot11 适配器收到原始 JSON
    │
    ▼
normalize_onebot11_payload()
    │
    ▼
Rust: normalize_onebot11_event()  // 高性能 C/Rust 实现
    │
    ▼
Python: Event.from_dict()         // 转为 Event 对象
```

## 与上层的关系

```
┌──────────────────────────────────┐
│   Python 层                       │
│   message.py  ← Python 封装       │
│   event.py    ← 使用 core.py      │
│   onebot11.py ← 使用 core.py      │
└──────────────┬───────────────────┘
               │ import
┌──────────────▼───────────────────┐
│   core.py  (薄封装层)              │
│   merge_dicts / new_event_id /    │
│   normalize_onebot11_payload      │
└──────────────┬───────────────────┘
               │ import
┌──────────────▼───────────────────┐
│   _core (Rust/PyO3 原生扩展)      │
│   CoreMessage / deep_merge_json   │
│   next_event_id /                 │
│   normalize_onebot11_event        │
└──────────────────────────────────┘
```
