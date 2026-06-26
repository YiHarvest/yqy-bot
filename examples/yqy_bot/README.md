# YHarvest

基于 [iamai](https://github.com/retrofor/iamai) + NapCatQQ 的 QQ 个人聊天机器人，接入 DeepSeek V4 Flash。

## 快速开始

### 1. 安装依赖

```bash
cd D:\jiaodian\iamai
uv sync --package yqy-bot
```

### 2. 配置环境变量

在项目根目录创建 `.env` 文件：

```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

### 3. 启动 NapCatQQ

QQ 私聊需要 NapCatQQ 作为 OneBot11 适配器。

**① 找到 NapCat 安装目录**（通常在 `D:\LeStoreDownload\NapCat\`）：

```powershell
cd D:\LeStoreDownload\NapCat\NapCat.44498.Shell
```

**② 首次启动扫码登录**：

```powershell
.\NapCatWinBootMain.exe
```

> 会输出二维码链接，用手机 QQ 扫码登录。控制台乱码是编码问题，不影响使用。看到 `OneBot11 适配器初始化完成` 说明成功。

**③ 确认反向 WebSocket 已就绪**，监听地址为 `ws://127.0.0.1:8082/event`。

> NapCat 的 OneBot11 配置在 `versions/.../resources/app/napcat/config/` 下的 `napcat_<QQ号>.json` 中。

### 4. 启动机器人

确认 NapCat 已运行、WebSocket 已就绪后，在项目目录执行：

```bash
cd D:\jiaodian\iamai
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.onebot11.napcat.ws_reverse.toml
```

启动日志中看到 `onebot11 adapter starting in ws-reverse mode` 和 `onebot11 reverse websocket server listening on ws://127.0.0.1:8082/event` 即表示机器人已接入 QQ。

## 终端测试模式

如果临时没有 NapCatQQ，可用终端模式在命令行里对话，无需启动 NapCat：

```bash
cd D:\jiaodian\iamai
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.terminal.toml
```

## 目录结构

```
yqy_bot/
├── config/                  # 所有配置文件（JSON）
│   ├── prompt.json          # 人设提示词
│   ├── mood.json            # 情绪系统参数
│   ├── relation.json        # 关系系统参数
│   ├── behavior.json        # 行为决策权重
│   ├── active_life.json     # 主动行为参数
│   ├── reflection.json      # 反思系统参数
│   ├── memory_filter.json   # 记忆过滤规则
│   ├── bot.json             # 通用参数
│   └── users.json           # 用户身份映射
├── services/                # 业务逻辑层
│   ├── mood.py              # 情绪服务
│   ├── relation.py          # 关系服务
│   ├── behavior.py          # 行为决策引擎
│   ├── memory.py            # 长期记忆
│   ├── memory_filter.py     # 记忆过滤
│   ├── reflection.py        # 反思系统
│   ├── history.py           # 聊天历史
│   └── db.py                # SQLite 数据库
├── src/yqy_bot/plugins/
│   ├── chat.py              # 核心聊天插件
│   └── active_life.py       # 主动行为插件
├── run.py                   # 启动入口
└── pyproject.toml
```

## 功能特性

- **自由聊天** — 带人设的 DeepSeek 对话，自动选择文字/表情/斗图回复
- **上下文记忆** — 多轮对话历史 + SQLite 持久化
- **长期记忆** — LLM 提取用户重要事实
- **情绪系统** — mood / energy / loneliness 随时间衰减
- **关系系统** — 好感度 / 亲密度 / 信任度，不同身份不同语气
- **行为决策引擎** — 加权随机选择 chat / meme / poke / silent
- **主动行为** — 孤独或精力足够时主动联系用户
- **反思系统** — 从聊天中提炼观察和判断


uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.onebot11.napcat.ws_reverse.toml

