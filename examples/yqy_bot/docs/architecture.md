# yqy_bot 架构与链路说明

本文档详细说明 yqy_bot 项目的整体架构、消息处理链路，以及它如何依托 iamai 框架构建。

---

## 1. 项目定位

yqy_bot 是一个基于 **iamai 框架** + **NapCatQQ** 的 QQ 聊天机器人 MVP。

核心特点：
- 被动回复（只在合适时机响应）
- LLM 驱动的智能对话
- 用户/群聊画像与记忆系统
- 多角色 LLM 路由（chat/intent/reason/vision/background）

---

## 2. iamai 框架能力

iamai 是一个跨平台聊天机器人框架，采用 **Rust (PyO3) + Python** 架构，提供三层结构：

```
┌─────────────────────────────────────────────────────┐
│                    Plugin 层                         │
│              业务逻辑 (yqy_bot 实现)                  │
├─────────────────────────────────────────────────────┤
│                    Runtime 层                        │
│         插件调度 / 事件分发 / 状态管理 / 热重载        │
├─────────────────────────────────────────────────────┤
│                    Adapter 层                        │
│        平台协议 (OneBot11/Terminal/Telegram/Webhook) │
└─────────────────────────────────────────────────────┘
```

### iamai 核心模块

| 模块 | 作用 | yqy_bot 使用方式 |
|------|------|-----------------|
| `Runtime` | 中央调度器 - 加载插件/适配器、事件分发、生命周期管理 | 入口点：`Runtime.from_config_file()` |
| `Plugin` | 插件基类 + 装饰器 (`@command`, `@message_handler`) | `ChatPlugin` 继承此基类 |
| `Context` | 处理器回调参数，包含事件信息和回复方法 | `ctx.reply(Message(...))` 发送消息 |
| `Message` | 消息构建器，支持文本、表情、图片等 | 构建 QQ 消息回复 |
| `Event` | 标准化事件模型（适配器无关） | `ctx.event` 获取消息详情 |
| `message_handler` | 消息事件匹配装饰器 | `@message_handler(priority=50)` |
| `Adapter` | 平台协议适配（OneBot11/Terminal） | 配置文件指定适配器类型 |

---

## 3. yqy_bot 对 iamai 的依赖关系

### 入口集成

```python
# run.py
from iamai import Runtime
from iamai.config import load_env_file

runtime = Runtime.from_config_file(args.config)
asyncio.run(runtime.serve())
```

**流程**：
1. 加载 `.env` 环境变量（各角色 LLM API 配置）
2. 从 TOML 配置文件创建 `Runtime`
3. 运行 `runtime.serve()` 启动事件循环

### 插件集成

```python
# src/yqy_bot/plugins/chat.py
from iamai import Context, Message, Plugin, message_handler

class ChatPlugin(Plugin):
    name = "chat"

    async def startup(self) -> None:
        # 初始化 ChatPipeline，存入 runtime.state
        self.runtime.state["yqy_bot.pipeline"] = ChatPipeline.from_runtime(...)

    @message_handler(priority=50)
    async def passive_chat(self, ctx: Context) -> None:
        pipeline = self.runtime.state.get("yqy_bot.pipeline")
        await pipeline.process_message(ctx.event.to_dict(), sender=_ContextSender(ctx))
```

**关键点**：
- 继承 `iamai.Plugin` 基类
- 使用 `@message_handler` 装饰器匹配消息事件
- 通过 `self.runtime.state` 存储共享状态
- 通过 `ctx.reply()` 发送回复

### 配置集成

```toml
# config.onebot11.napcat.ws_reverse.toml
[runtime]
adapters = ["onebot11"]
plugin_dirs = ["src/yqy_bot/plugins"]
python_paths = ["src"]
superusers = ["..."]
hot_reload = {enabled = true, interval = 1.0}

[adapter.onebot11]
mode = "ws-reverse"
host = "0.0.0.0"
port = 8082
access_token = "..."
```

**配置作用**：
- `plugin_dirs`：告诉 iamai 插件发现路径
- `python_paths`：Python 模块搜索路径
- `adapters`：指定加载的适配器
- `hot_reload`：开发时热重载

---

## 4. 消息处理链路

### 整体流程图

```
┌────────────────────────────────────────────────────────────────────┐
│                         iamai 框架层                                │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐    ┌──────────┐ │
│  │ Adapter  │ -> │ Runtime  │ -> │ ChatPlugin   │ -> │ Context  │ │
│  │(OneBot11)│    │ 事件分发  │    │ @message_    │    │  reply() │ │
│  │          │    │          │    │ handler      │    │          │ │
│  └──────────┘    └──────────┘    └──────────────┘    └──────────┘ │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────┐
│                        yqy_bot 业务层                               │
│                                                                     │
│  ┌──────────────┐                                                 │
│  │ ChatPipeline │ ──> process_message()                           │
│  └──────────────┘                                                 │
│         │                                                          │
│         ▼                                                          │
│  ┌──────────────┐   ┌───────────────┐   ┌───────────────────┐    │
│  │ MessageParser│ ->│ ReplyGate     │ ->│ IntentRouter      │    │
│  │ (qq/parser)  │   │ 冷却/阈值判断  │   │ 回复决策/风格判断 │    │
│  └──────────────┘   └───────────────┘   └───────────────────┘    │
│                                                │                   │
│                                                ▼                   │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                    ContextBuilder                              │ │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────┐ │ │
│  │  │ History │  │ Profile │  │ Memory  │  │ Reflection      │ │ │
│  │  │ 历史消息 │  │ 用户画像 │  │ 记忆    │  │ 反思摘要        │ │ │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────────────┘ │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                │                   │
│                                                ▼                   │
│  ┌───────────────────┐   ┌───────────────────┐                    │
│  │ PromptBuilder     │ ->│ LLMRouter         │                    │
│  │ 构建 LLM Prompt   │   │ 按角色路由 LLM    │                    │
│  └───────────────────┘   └───────────────────┘                    │
│                                │                                   │
│                                ▼                                   │
│  ┌───────────────────┐   ┌───────────────────┐                    │
│  │ ResponseGenerator │ ->│ SafetyGuard       │                    │
│  │ LLM 生成回复      │   │ 毒性/假事实检测   │                    │
│  └───────────────────┘   └───────────────────┘                    │
│                                │                                   │
│                                ▼                                   │
│  ┌───────────────────┐                                            │
│  │ QQSender          │ ──> ctx.reply(Message(...))                │
│  │ 构建 QQ 消息      │                                            │
│  └───────────────────┘                                            │
└────────────────────────────────────────────────────────────────────┘
```

### 链路各阶段说明

| 阶段 | 模块 | 文件 | 功能 |
|------|------|------|------|
| **消息接入** | Adapter | iamai 内置 | OneBot11 适配器接收 QQ 事件，标准化为 `Event` |
| **事件分发** | Runtime | iamai 内置 | 将事件分发到匹配的插件处理器 |
| **插件入口** | ChatPlugin | `plugins/chat.py` | `@message_handler` 捕获消息，调用 Pipeline |
| **消息解析** | MessageParser | `qq/parser.py` | 解析 OneBot11 消息结构，提取文本/图片/引用等 |
| **回复门控** | ReplyGate | `core/pipeline.py` | 冷却时间、群聊热度阈值、触发关键词判断 |
| **意图路由** | IntentRouter | `intent/router.py` | 决定是否回复、回复风格、是否需要 reason model |
| **上下文构建** | ContextBuilder | `context/builder.py` | 组装历史消息、用户画像、记忆、反思摘要 |
| **Prompt 构建** | PromptBuilder | `context/prompt.py` | 生成 LLM 输入 Prompt |
| **LLM 路由** | LLMRouter | `core/llm_router.py` | 按角色（chat/intent/reason/vision）路由到不同 LLM |
| **响应生成** | ResponseGenerator | `response/generator.py` | LLM 生成结构化 JSON 回复 |
| **安全检测** | SafetyGuard | `safety/guard.py` | 检测毒性表达、假事实，必要时重写 |
| **消息发送** | QQSender | `qq/sender.py` | 构建回复 Message，调用 `ctx.reply()` |

---

## 5. yqy_bot 自建模块（非 iamai 提供）

这些是 yqy_bot 的业务逻辑实现：

| 模块目录 | 核心文件 | 功能 |
|----------|----------|------|
| `core/` | `models.py`, `config.py`, `pipeline.py`, `llm_router.py`, `cooldown.py` | 数据模型、配置加载、Pipeline 调度、LLM 路由、冷却控制 |
| `intent/` | `router.py` | 意图分类（回复决策、风格判断） |
| `response/` | `generator.py` | LLM 响应生成（JSON 输出） |
| `context/` | `builder.py`, `prompt.py` | 上下文组装、Prompt 构建 |
| `safety/` | `guard.py` | 输出安全检测与重写 |
| `qq/` | `parser.py`, `sender.py`, `emoji.py`, `human.py`, `napcat_client.py`, `napcat_tools.py` | QQ 消息解析、发送、表情服务、人类行为模拟、NapCat API |
| `storage/` | `database.py`, `repositories.py` | SQLite 持久化（历史、画像、记忆、摘要） |
| `background/` | `worker.py` | 后台慢速学习任务（画像更新、摘要生成） |

### 目录结构

```text
src/yqy_bot/
├── __init__.py              # 包初始化文件
├── plugins/
│   └── chat.py              # iamai 插件入口
├── core/
│   ├── models.py            # 数据模型定义
│   ├── config.py            # 配置加载
│   ├── pipeline.py          # Pipeline 调度核心
│   ├── llm_router.py        # LLM 路由器
│   └── cooldown.py          # 冷却时间控制
├── intent/
│   └── router.py            # 意图路由
├── response/
│   └── generator.py         # 响应生成
├── context/
│   ├── builder.py           # 上下文构建
│   └── prompt.py            # Prompt 构建
├── safety/
│   └── guard.py             # 安全检测
├── qq/
│   ├── parser.py            # OneBot11 消息解析
│   ├── sender.py            # QQ 消息发送
│   ├── emoji.py             # 表情服务
│   ├── human.py             # 人类行为模拟（打字延迟）
│   ├── napcat_client.py     # NapCat HTTP 客户端
│   └── napcat_tools.py      # NapCat API 工具
├── storage/
│   ├── database.py          # SQLite 数据库
│   └── repositories.py      # 数据仓库
└── background/
    └── worker.py            # 后台任务处理器
```

### 导入方式

项目采用**绝对导入**（`from yqy_bot.xxx import ...`），而非相对导入（`from ..xxx import ...`）。

运行时需确保 `PYTHONPATH` 包含 `src` 目录：

| 运行方式 | PYTHONPATH 配置 |
|----------|----------------|
| 本地运行 | iamai TOML 配置 `python_paths = ["src"]` 自动设置 |
| Docker 运行 | `docker-compose.yml` 中 `PYTHONPATH: /workspace/iamai/examples/yqy_bot/src:/workspace/iamai/python` |

---

## 6. 数据持久化

yqy_bot 使用 SQLite 存储以下数据：

| 表 | 内容 | 更新时机 |
|----|------|----------|
| `history` | 聊天历史记录 | 每条消息 |
| `profiles` | 用户/群聊画像 | 后台任务周期更新 |
| `summaries` | 对话摘要 | 后台任务周期生成 |
| `memories` | 用户偏好记忆 | 后台任务周期提取 |
| `reflections` | 对话反思 | 后台任务周期生成 |

---

## 7. 多角色 LLM 配置

yqy_bot 支持按功能角色配置不同的 LLM：

```env
# .env 环境变量
CHAT_OPENAI_BASE_URL=      # 主聊天模型
CHAT_OPENAI_API_KEY=
CHAT_OPENAI_MODEL=

INTENT_OPENAI_BASE_URL=    # 意图分类模型
INTENT_OPENAI_API_KEY=
INTENT_OPENAI_MODEL=

REASON_OPENAI_BASE_URL=    # 深度推理模型
REASON_OPENAI_API_KEY=
REASON_OPENAI_MODEL=

VISION_OPENAI_BASE_URL=    # 视觉模型（图片理解）
VISION_OPENAI_API_KEY=
VISION_OPENAI_MODEL=

BACKGROUND_OPENAI_BASE_URL= # 后台任务模型
BACKGROUND_OPENAI_API_KEY=
BACKGROUND_OPENAI_MODEL=
```

---

## 8. 运行方式

### 终端模式（本地调试）

```bash
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.terminal.toml
```

### NapCat 模式（QQ 连接）

```bash
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.onebot11.napcat.ws_reverse.toml
```

### Docker Compose（生产部署）

```bash
cd examples/yqy_bot
docker compose up -d --build
docker compose logs -f qqbot
```

---

## 9. 总结

**iamai 提供**：
- 框架基础设施
- Adapter 协议处理（OneBot11/Terminal）
- Runtime 插件调度、事件分发、热重载
- Plugin 基类 + 装饰器注册机制
- 标准化 Event/Message/Context 对象
- 配置系统（TOML + Pydantic）

**yqy_bot 实现**：
- 业务逻辑
- 完整的聊天 Pipeline（解析 → 门控 → 意图 → 上下文 → LLM → 安全 → 发送）
- LLM 路由与响应生成
- 用户画像、记忆、反思系统
- SQLite 持久化
- NapCat API 集成
- 后台慢速学习任务

两者关系：**iamai 是骨架，yqy_bot 是血肉**。