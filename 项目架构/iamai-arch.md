# iamai 项目架构文档

## 一、项目概览

iamai 是一个**跨平台聊天机器人框架**，采用 **Rust 核心 (PyO3) + Python 插件系统** 的混合架构。

- **Rust 层**：提供消息链（`CoreMessage`）、事件 ID 生成、JSON 深度合并等高性能基础能力
- **Python 层**：提供适配器、运行时编排、插件系统、LLM Agent、规则引擎、权限控制等完整功能

## 二、核心架构流程图

```
┌──────────────────────────────────────────────────────────────────────┐
│                           config.toml                                 │
│                    (配置文件：适配器、插件、规则)                       │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ 加载
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          Runtime (runtime.py)                         │
│  运行时容器：加载配置 → 创建适配器/插件 → 启动事件循环                    │
│                                                                       │
│  bootstrap()  ─→ load_plugins()  ─→ load_adapters()                   │
│       │                                                                 │
│       ▼                                                                 │
│  serve()  ─→ start_adapters  ─→ 事件循环等待                             │
└───────┬───────────────────────────┬──────────────┬───────────────────┘
        │                           │               │
        ▼                           ▼               ▼
┌──────────────┐   ┌────────────────────────┐   ┌──────────────┐
│   Adapter    │   │       Plugin 系统       │   │   LLM Agent  │
│ (adapter.py) │   │    (plugin.py)          │   │  (agent.py)  │
│              │   │                        │   │              │
│ OneBot11 ◄───┼───┤  · @command            │   │  LLMClient   │
│ Terminal     │   │  · @message_handler    │   │  LLMConfig   │
│ Telegram     │   │  · @event_handler      │   │  ToolRegistry│
│ Webhook      │   │  · @middleware         │   │  Guardrail   │
└──────┬───────┘   └───────────┬────────────┘   └──────┬───────┘
       │                       │                       │
       │ 接收外部事件            │ 处理回调函数            │ 大模型对话
       │                       │                       │
       ▼                       ▼                       ▼
┌──────────────────────────────────────────────────────────────┐
│                    消息与事件层                                 │
│                                                               │
│  Event (event.py)     ← 统一事件模型                            │
│  Message (message.py) ← 消息链（底层 Rust CoreMessage）         │
│  Context (context.py) ← 处理上下文（reply/send/call_api）       │
│                                                               │
│            ▼  ▼  ▼        Rust 核心层                          │
│  ┌──────────────────────────────────────────────────┐         │
│  │  CoreMessage (Rust/PyO3)                          │         │
│  │  · from_onebot11_json / to_onebot11_json          │         │
│  │  · push_text / push / plain_text / render_text    │         │
│  │  · deep_merge_json / normalize_onebot11_event     │         │
│  └──────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                     辅助系统                                    │
│                                                               │
│  observability.py  → 指标计数 + 审计日志                        │
│  config.py         → TOML 配置加载和校验                        │
│  rules.py          → 规则引擎（startswith/regex/any_of 等）     │
│  permissions.py    → 权限控制（superusers/user_in 等）          │
│  middleware.py     → 中间件阶段（before/around/after/error）     │
│  session.py        → 会话管理（等待用户回复）                    │
│  di.py             → 依赖注入                                   │
│  state.py          → 状态持久化                                 │
└──────────────────────────────────────────────────────────────┘
```

## 三、事件处理流程

```
外部平台(NapCatQQ/TG)
    │
    ▼
Adapter.start() ──接收原始payload ──→ normalize_payload() ──→ Event
    │                                                           │
    │                                                           ▼
    │                                              Runtime.dispatch(event)
    │                                                           │
    │                                                           ▼
    │                                          匹配 Handlers (规则/权限检查)
    │                                                           │
    │                                                           ▼
    │                                       中间件管道 (before → around → after)
    │                                                           │
    │                                                           ▼
    │                                        Plugin 回调执行 (你的业务逻辑)
    │                                                           │
    │                                                           ▼
    │                                        Context.reply() → Adapter.send_message()
    │                                                           │
    ▼                                                           ▼
外部平台  ◄──────────────────────────────────  响应消息
```

## 四、目录结构

```
iamai/
├── src/                          # Rust 核心（PyO3）
│   └── lib.rs                    # CoreMessage, normalize_onebot11_event 等
├── python/iamai/                 # Python 包
│   ├── __init__.py               # 公共导出
│   ├── runtime.py                # ★ 运行时容器
│   ├── agent.py                  # ★ LLM Agent 客户端
│   ├── adapter.py                # ★ 适配器基类
│   ├── observability.py          # ★ 可观测性（指标+审计）
│   ├── core.py                   # ★ Rust 核心 Python 封装
│   ├── context.py                # 处理上下文
│   ├── event.py                  # 事件模型
│   ├── message.py                # 消息链
│   ├── plugin.py                 # 插件装饰器
│   ├── config.py                 # 配置加载
│   ├── rules.py                  # 规则引擎
│   ├── permissions.py            # 权限控制
│   ├── middleware.py             # 中间件装饰器
│   ├── session.py                # 会话管理
│   ├── di.py                     # 依赖注入
│   ├── state.py                  # 状态持久化
│   ├── adapters/                 # 适配器实现
│   │   ├── middleware.py         # 适配器中间件基类
│   │   ├── onebot11.py           # OneBot11 适配器
│   │   ├── terminal.py           # 终端适配器
│   │   └── ...
│   └── plugins/                  # 内置插件
│       ├── management.py         # 管理插件
│       └── management_api.py     # API 管理插件
├── examples/                     # 示例 runtime
│   └── yqy_bot/                  # YQY 的聊天机器人
│       ├── run.py                # 入口脚本
│       ├── pyproject.toml        # 依赖声明
│       ├── config.terminal.toml  # 终端模式配置
│       ├── config.onebot11.*.toml# QQ 模式配置
│       └── src/yqy_bot/plugins/
│           └── chat.py           # 核心对话插件
├── docs/                         # 文档
│   ├── iamai-arch.md             # 架构文档（本文件）
│   ├── runtime.md                # runtime.py 说明
│   ├── agent.md                  # agent.py 说明
│   ├── adapter.md                # adapter.py 说明
│   ├── observability.md          # observability.py 说明
│   └── core.md                   # core.py 说明
└── pyproject.toml                # 项目配置
```

## 五、关键设计模式

| 模式 | 应用位置 | 说明 |
|------|---------|------|
| **适配器模式** | adapter.py | 统一抽象层，屏蔽不同平台差异 |
| **中间件管道** | runtime.py → middleware | before/around/after/error 四阶段 |
| **依赖注入** | di.py | 插件通过类型注解自动注入依赖 |
| **装饰器注册** | plugin.py | @command/@message_handler 声明式注册 |
| **观察者模式** | SessionManager | 等待用户下一轮对话 |
| **Pydantic 校验** | config.py | TOML 配置自动验证 |
| **Rust 核心层** | core.py → _core | PyO3 提供高性能消息处理 |
