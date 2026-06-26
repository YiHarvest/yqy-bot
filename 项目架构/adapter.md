# adapter.py - 适配器基类

## 概述

`adapter.py` 定义了 iamai 的**适配器抽象基类**。适配器是 iamai 与外部平台之间的桥梁——负责接收外部事件并将其标准化为 iamai 内部事件，同时将内部消息发送回外部平台。

**文件位置**：[`python/iamai/adapter.py`](../python/iamai/adapter.py)

## 核心抽象 `Adapter`

```python
class Adapter(ABC):
    """iamai 与外部协议/运行时之间的传输桥梁。"""

    name = "adapter"                    # 适配器名称
    runtime: Runtime                    # 所属运行时
    config: dict[str, Any]              # 适配器配置
    logger: logging.Logger              # 适配器日志器

    @abstractmethod
    async def start() -> None           # 启动适配器，开始接收事件
    async def close() -> None           # 关闭适配器资源
    @abstractmethod
    async def send_message(message, *, event?, target?) -> Any  # 发送消息
    async def call_api(action, **params) -> Any                 # 调用 API
    async def emit(event: Event) -> None                        # 提交事件到运行时
```

## 生命周期

```
Runtime.bootstrap()
    │
    ├── Adapter.__init__(runtime, config)
    │
    ▼
Runtime.serve()
    │
    ├── Adapter.start()     // WebSocket 连接 / HTTP 监听
    │       │
    │       ├── 收到外部事件
    │       ├── normalize_payload()  → Event
    │       └── emit(event)          → Runtime.dispatch()
    │
    ▼
Runtime.shutdown()
    │
    └── Adapter.close()    // 断开连接
```

## 消息发送流程

```
Plugin 业务代码
    │
    ├── ctx.reply("你好")
    │
    ▼
Adapter.send_message(message, event=event)
    │
    ├── 解析目标 (私聊/群聊/频道)
    ├── 编码消息为平台格式
    └── 通过 WebSocket/HTTP API 发送
```

## 已有适配器实现

| 适配器 | 类 | 协议 | 用途 |
|--------|---|------|------|
| OneBot11 | `OneBot11Adapter` | WebSocket (正向/反向) | QQ 平台 (NapCatQQ/LLOneBot) |
| Terminal | `TerminalAdapter` | stdin/stdout | 本地终端测试 |
| Telegram | `TelegramAdapter` | HTTP Webhook | Telegram Bot API |
| Webhook | `WebhookAdapter` | HTTP | 通用 HTTP 事件源 |

## 适配器的核心机制

所有适配器共享 `ModeSwitchingAdapterMiddleware`（[`adapters/middleware.py`](../python/iamai/adapters/middleware.py)），提供：

- **传输模式切换**：ws-client（正向 WS）/ ws-reverse（反向 WS）/ http-webhook
- **事件标准化**：将不同平台的原始 payload 转为统一的 `Event` 模型
- **消息编码**：将内部的 `Message` 转为平台特定的消息格式（如 OneBot11 数组格式）
- **API 调用**：通过 HTTP/WS 调用平台 API（如发送私聊消息、获取群信息等）
