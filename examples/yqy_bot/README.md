# YQY_BOT

基于 `iamai` + NapCatQQ 的 QQ 被动聊天机器人 MVP。

## 目标


`消息接入 -> MessageParser -> ReplyGate -> IntentRouter -> ContextBuilder -> PromptBuilder -> LLMRouter -> ResponseGenerator -> FactGuard/Safety -> QQSender`

后台只做慢速学习：

- 用户画像
- 群聊画像
- 摘要
- 记忆
- 反思

## 配置

项目只保留 3 个 JSON 配置文件：

- `config/bot.json`
- `config/persona.json`
- `config/safety.json`

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
## 日常启动流程：

```zsh
cd /home/yqy/Projects/iamai/examples/yqy_bot
# 改完本地代码后
docker compose restart qqbot
docker compose logs -f --tail=80 qqbot
```
说明：
- 代码会以挂载方式进入容器，改完宿主机代码后直接重启容器即可生效
- 容器内默认通过 `host.docker.internal:3000` 访问宿主机上的 NapCat HTTP 服务
- 如果你本机没有把 `host.docker.internal` 映射到宿主机，请保留 compose 里的 `extra_hosts` 配置

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
