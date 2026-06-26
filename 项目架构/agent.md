# agent.py - LLM Agent 客户端

## 概述

`agent.py` 提供了 **OpenAI 兼容 API 的异步客户端**，支持文本对话、JSON 结构化输出、工具调用和输出护栏。是插件中调用大模型的核心基础设施。

**文件位置**：[`python/iamai/agent.py`](../python/iamai/agent.py)

## 核心类

### `LLMConfig` — 大模型配置

```python
@dataclass
class LLMConfig:
    api_key: str = ""         # API 密钥
    base_url: str | None      # API 地址（留空用 OpenAI 官方）
    model: str                # 模型名称
    temperature: float = 0.7  # 温度参数
    max_tokens: int = 800     # 最大 token 数
    timeout: float = 60.0     # 请求超时
```

**环境变量自动加载**：
- `OPENAI_API_KEY` → `api_key`
- `OPENAI_BASE_URL` → `base_url`
- `OPENAI_MODEL` → `model`

### `LLMClient` — 大模型客户端

```python
class LLMClient:
    async def chat_text(messages, temperature?, max_tokens?, trace?) -> str
    async def chat_json(messages, schema?, trace?) -> dict | list
```

| 方法 | 返回类型 | 说明 | 适用场景 |
|------|---------|------|---------|
| `chat_text()` | `str` | 纯文本回复 | 普通对话 |
| `chat_json()` | `dict/list` | JSON 结构化回复 | 需要解析的返回（如下表选择、事件分类） |

### `ToolRegistry` — 工具注册中心

```python
class ToolRegistry:
    def register(name, description, callback, ...)  # 注册工具
    async def call(name, tool_input, ...) -> Any    # 调用工具
    def describe() -> str                           # 列举工具
    def list_tools() -> list[dict]                  # 元数据
```

支持审批流程：`requires_approval=True` 时需要回调确认才能执行。

### `Guardrail` — 输出护栏

```python
class Guardrail:
    def check(text: str) -> None   # 检查输出是否包含禁用词
```

### `AgentTrace` — 调用追踪

```python
class AgentTrace:
    def add(kind, name, input?, output?, **metadata)  # 添加记录
    def mark(status: str)                              # 标记状态
    def lines(limit=12) -> list[str]                   # 人类可读输出
    def to_dict() -> dict                              # JSON 输出
```

## 关键函数

| 函数 | 说明 |
|------|------|
| `extract_json_value(text)` | 从模型输出中提取 JSON 对象（支持 markdown fence 和平衡括号搜索） |
| `clip_text(value, limit=280)` | 压缩空白并截断文本 |
| `format_transcript(lines, limit=10)` | 格式化对话文本 |

## 使用示例

```python
# 在插件中使用
config = LLMConfig.from_mapping()
client = LLMClient(config)

# 纯文本对话
reply = await client.chat_text([
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "你好"},
])

# JSON 结构化输出
result = await client.chat_json([
    {"role": "system", "content": "返回JSON"},
    {"role": "user", "content": "今天天气如何"},
])
# result = {"text": "今天天气不错", "face_id": "74"}
```

## 错误处理

```python
class AgentError(RuntimeError):
    """当 Agent 操作失败时抛出的异常"""
```
