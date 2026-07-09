# YQY_BOT

基于 `iamai` 框架 + NapCatQQ 的智能 QQ 聊天机器人。

## 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                         yqy_bot 业务层                           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────┐  │
│  │ ChatPipeline│ │ContextBuilder│ │LLMRouter   │ │Background │  │
│  │ (消息处理)   │ │ (上下文组装) │ │ (多角色路由)│ │Worker     │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                       iamai 框架层                               │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌───────────┐  │
│  │   Runtime   │ │   Plugin    │ │   Context   │ │ OneBot11  │  │
│  │ (运行时容器) │ │ (插件基类)   │ │ (HandlerCtx)│ │ Adapter   │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 消息处理流程

```
消息接入 -> MessageParser -> ReplyGate -> IntentRouter -> ContextBuilder -> PromptBuilder -> LLMRouter -> ResponseGenerator -> SafetyGuard -> QQSender
```

### 上下文组装

每次回复前，`ContextBuilder` 并行加载以下组件：

| 组件 | 来源 | 说明 |
|------|------|------|
| Recent History | `chat_history` 表 | 群聊 20 条 / 私聊 12 条 |
| Chat Summary | `chat_summary` 表 | 历史对话压缩 |
| User Profile | `user_profile` 表 | 偏好、风格、关注点 |
| Group Profile | `group_profile` 表 | 群风格、话题、活跃者 |
| Memories | `memory` 表 | 分数 ≥ 0.35 的相关记忆 |
| Reflections | `reflection` 表 | LLM 提取的反思 |

### 后台学习

响应发送后，`BackgroundWorker` 异步处理：

- 用户画像更新（偏好、关注点）
- 群画像更新（话题、活跃者）
- 聊天摘要压缩
- 记忆提取与评分
- 反思记录

### 群热度自适应

根据群聊活跃度动态调整回复策略：

| 状态 | 消息数/5分钟 | 回复条件 |
|------|-------------|---------|
| quiet | < 3 | @、回复、关键词、问题 |
| active | 3-6 | @、回复、关键词、强问题 |
| hot | 6-12 | 仅 @ 或回复 |
| flood | > 12 | 仅 @ 且短回复 |

### LLM 多角色路由

不同任务使用独立的 LLM 配置：

| 角色 | 用途 | 环境变量前缀 |
|------|------|-------------|
| chat | 日常聊天 | `CHAT_OPENAI_*` |
| intent | 意图决策 | `INTENT_OPENAI_*` |
| reason | 情感推理 | `REASON_OPENAI_*` |
| vision | 图像理解 | `VISION_OPENAI_*` |
| background | 后台学习 | `BACKGROUND_OPENAI_*` |
| long_context | 长上下文 | `LONG_CONTEXT_OPENAI_*` |

## 配置

项目配置文件：

- `config/bot.json` - 行为参数（冷却、热度阈值、超级用户等）
- `config/persona.json` - 人设、说话风格、示例
- `config/safety.json` - 安全边界

## 环境变量

按角色分别配置：

```env
CHAT_OPENAI_BASE_URL=
CHAT_OPENAI_API_KEY=
CHAT_OPENAI_MODEL=

INTENT_OPENAI_BASE_URL=
INTENT_OPENAI_API_KEY=
INTENT_OPENAI_MODEL=

REASON_OPENAI_BASE_URL=
REASON_OPENAI_API_KEY=
REASON_OPENAI_MODEL=

VISION_OPENAI_BASE_URL=
VISION_OPENAI_API_KEY=
VISION_OPENAI_MODEL=

BACKGROUND_OPENAI_BASE_URL=
BACKGROUND_OPENAI_API_KEY=
BACKGROUND_OPENAI_MODEL=

LONG_CONTEXT_OPENAI_BASE_URL=
LONG_CONTEXT_OPENAI_API_KEY=
LONG_CONTEXT_OPENAI_MODEL=
```

可选地放一个项目根目录 `.env`，`run.py` 会自动加载。

## 运行

终端模式：

```bash
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.terminal.toml
```

NapCat 模式：

```bash
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.onebot11.napcat.ws_reverse.toml
```

Docker Compose 开发模式：

```bash
cd examples/yqy_bot
docker compose up -d --build
docker compose logs -f qqbot
```

日常启动流程：

```zsh
cd /home/yqy/Projects/iamai/examples/yqy_bot
# 改完本地代码后
docker compose restart qqbot
docker compose logs -f qqbot
```

说明：
- 代码以挂载方式进入容器，改完宿主机代码后直接重启容器即可生效
- 容器内默认通过 `host.docker.internal:3000` 访问宿主机上的 NapCat HTTP 服务
- 若本机没有 `host.docker.internal` 映射，请保留 compose 里的 `extra_hosts` 配置

## 目录

```text
src/yqy_bot/
├── __init__.py
├── plugins/
│   └── chat.py
├── core/
│   ├── models.py
│   ├── config.py
│   ├── pipeline.py
│   ├── llm_router.py
│   └── cooldown.py
├── intent/
│   └── router.py
├── response/
│   └── generator.py
├── context/
│   ├── builder.py
│   └── prompt.py
├── safety/
│   └── guard.py
├── qq/
│   ├── parser.py
│   ├── sender.py
│   ├── emoji.py
│   ├── human.py
│   ├── napcat_client.py
│   └── napcat_tools.py
├── storage/
│   ├── database.py
│   └── repositories.py
└── background/
    └── worker.py
```

## 导入方式

项目采用**绝对导入**（`from yqy_bot.xxx import ...`），而非相对导入（`from ..xxx import ...`）。

运行时需确保 `PYTHONPATH` 包含 `src` 目录：

- 本地运行：iamai 的 TOML 配置中 `python_paths = ["src"]` 已自动设置
- Docker 运行：`docker-compose.yml` 中已配置 `PYTHONPATH: /workspace/iamai/examples/yqy_bot/src:/workspace/iamai/python`

## 架构文档

详细架构说明见 [docs/architecture.md](docs/architecture.md)。
