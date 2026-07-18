# YQY_BOT

基于 `iamai` 框架 + NapCatQQ 的智能 QQ 聊天机器人。

## 架构概览

### 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            yqy_bot 业务层                                    │
│                                                                             │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐        │
│  │  ChatPipeline     │  │ ContextBuilder   │  │   LLMRouter      │        │
│  │  (消息处理管道)    │  │ (上下文组装)      │  │ (多角色路由)     │        │
│  │                   │  │                  │  │                  │        │
│  │  - 消息解析       │  │  - 历史加载      │  │  - chat          │        │
│  │  - 门控判断       │  │  - 画像加载      │  │  - intent        │        │
│  │  - 意图决策       │  │  - 记忆提取      │  │  - reason        │        │
│  │  - 流程调度       │  │  - 反思加载      │  │  - vision        │        │
│  └──────────────────┘  └──────────────────┘  │  - background    │        │
│                                                │  - long_context  │        │
│  ┌──────────────────┐  ┌──────────────────┐  └──────────────────┘        │
│  │ BackgroundWorker │  │   SafetyGuard    │                              │
│  │ (后台学习)        │  │ (安全检查)       │                              │
│  │                  │  │                  │                              │
│  │  - 画像更新      │  │  - 毒性检测      │  ┌──────────────────┐        │
│  │  - 记忆提取      │  │  - 社交边界      │  │  SocialSafety    │        │
│  │  - 摘要压缩      │  │  - 事实检查      │  │  (社交边界控制)   │        │
│  │  - 反思记录      │  │  - 改写降级      │  │                  │        │
│  └──────────────────┘  └──────────────────┘  │  - 调侃强度      │        │
│                                                │  - 攻击检测      │        │
│  ┌──────────────────┐  ┌──────────────────┐  │  - 引战检测      │        │
│  │ SearchMCPClient  │  │  MemoryUtils     │  │  - 情绪感知      │        │
│  │ (MCP搜索客户端)   │  │ (记忆辅助工具)    │  └──────────────────┘        │
│  │                  │  │                  │                              │
│  │  - 网页搜索      │  │  - 噪音过滤      │                              │
│  │  - 内容抽取      │  │  - 角色识别      │                              │
│  │  - 查询清洗      │  │  - 分数计算      │                              │
│  └──────────────────┘  │  - 去重合并      │                              │
│                        └──────────────────┘                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                            iamai 框架层                                     │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Runtime    │  │    Plugin    │  │   Context    │  │ OneBot11     │  │
│  │ (运行时容器)  │  │ (插件基类)   │  │ (HandlerCtx) │  │ Adapter      │  │
│  │              │  │              │  │              │  │              │  │
│  │ - 生命周期   │  │ - Handler    │  │ - event      │  │ - WebSocket  │  │
│  │ - 状态管理   │  │ - state      │  │ - reply()    │  │ - 事件标准化 │  │
│  │ - 插件调度   │  │ - config     │  │ - send()     │  │ - 消息发送   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘  │
├─────────────────────────────────────────────────────────────────────────────┤
│                            数据存储层                                       │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         SQLite Database                               │  │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │  │
│  │  │chat_history │ │user_profile │ │   memory    │ │ reflection  │   │  │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘   │  │
│  │  ┌─────────────┐ ┌─────────────┐                                    │  │
│  │  │chat_summary │ │group_profile│                                    │  │
│  │  └─────────────┘ └─────────────┘                                    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────────┤
│                            外部服务层                                       │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                    │
│  │   NapCat     │  │    LLM       │  │   SearXNG    │                    │
│  │ (QQ协议适配)  │  │ (大语言模型)  │  │ (搜索后端)   │                    │
│  └──────────────┘  └──────────────┘  └──────────────┘                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 消息处理流程图

```
┌─────────┐
│ QQ消息  │
└────┬────┘
     ↓
┌─────────────────┐
│  NapCat 接收    │
│  WebSocket      │
└────────┬────────┘
         ↓
┌─────────────────┐       ┌─────────────────┐
│ iamai Runtime   │──────→│ ChatPlugin      │
│ 事件分发        │       │ message_handler │
└─────────────────┘       └────────┬────────┘
                                   ↓
                         ┌─────────────────────┐
                         │  ChatPipeline       │
                         │  process_message()  │
                         └──────────┬──────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         │                          │                          │
         ↓                          ↓                          ↓
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│ 消息解析        │      │ 群热度计算      │      │ 过滤检查        │
│ parse_message   │      │ group_heat      │      │ _is_ignored     │
└────────┬────────┘      └────────┬────────┘      └────────┬────────┘
         │                        │                        │
         └────────────────────────┴────────────────────────┘
                                  ↓
                         ┌─────────────────┐
                         │  回复门控       │
                         │  _reply_gate    │
                         │                 │
                         │  - 冷却检查     │
                         │  - 触发词匹配   │
                         │  - 热度策略     │
                         └────────┬────────┘
                                  ↓
                         ┌─────────────────┐
                         │  意图决策       │
                         │  IntentRouter   │
                         │  (LLM判断)      │
                         └────────┬────────┘
                                  ↓
                    ┌────────────────────────┐
                    │  搜索决策 (可选)       │
                    │  SearchMCPClient       │
                    │                        │
                    │  - 搜索意图判断        │
                    │  - URL抽取决策        │
                    │  - 查询清洗           │
                    └───────────┬────────────┘
                                ↓
              ┌──────────────────────────────────┐
              │  上下文组装 (并行加载)            │
              │  ContextBuilder                  │
              │                                  │
              │  ┌────────────────────────────┐ │
              │  │ Recent History (群20/私12) │ │
              │  │ Chat Summary               │ │
              │  │ User Profile               │ │
              │  │ Group Profile              │ │
              │  │ Memories (score ≥ 0.35)    │ │
              │  │ Reflections                │ │
              │  │ Search Result (可选)       │ │
              │  └────────────────────────────┘ │
              └──────────────┬───────────────────┘
                             ↓
              ┌──────────────────────────┐
              │  提示词构建               │
              │  PromptBuilder           │
              │                          │
              │  - 人设身份              │
              │  - 社交边界规则          │
              │  - 调侃强度提示          │
              │  - 上下文数据            │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │  LLM 调用                │
              │  LLMRouter.chat_text()   │
              │  (chat 角色)             │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │  回复生成                │
              │  ResponseGenerator       │
              │  (JSON解析)              │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │  安全检查                │
              │  SafetyGuard             │
              │                          │
              │  - 毒性检测              │
              │  - 社交边界检查          │
              │  - 事实边界检查          │
              │  - 不安全改写            │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │  表情解析 (可选)         │
              │  EmojiService            │
              │  - 表情包选择            │
              │  - 表情ID映射            │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │  人类化延迟              │
              │  HumanBehavior           │
              │  - 打字延迟计算          │
              │  - asyncio.sleep()       │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │  消息发送                │
              │  QQSender                │
              │  - 消息段构建            │
              │  - NapCat API调用        │
              └────────────┬─────────────┘
                           ↓
              ┌──────────────────────────┐
              │  后台学习 (异步)         │
              │  BackgroundWorker        │
              │                          │
              │  - 用户画像更新          │
              │  - 群画像更新            │
              │  - 记忆提取与评分        │
              │  - 摘要压缩              │
              │  - 反思记录              │
              └──────────────────────────┘
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
| Search Result | MCP 搜索 | 网页搜索或内容抽取结果（可选） |

## 核心模块说明

### 1. ChatPipeline (消息处理管道)

负责整个消息处理流程的调度和协调：

- **消息解析**：将 QQ 原始消息解析为结构化的 `ParsedMessage`
- **门控判断**：根据群热度、冷却时间、触发关键词决定是否回复
- **意图决策**：通过 LLM 判断用户意图和回复策略
- **流程调度**：协调各个模块完成消息处理

### 2. ContextBuilder (上下文组装器)

负责构建 LLM 提示所需的完整上下文：

- **并行加载**：使用 `asyncio.gather` 并行加载历史、画像、记忆等
- **冲突过滤**：移除与当前消息冲突的记忆和偏好
- **长度控制**：截断过长的提示，保留最重要的部分

### 3. SafetyGuard (安全检查器)

负责回复内容的安全检查和改写：

- **毒性检测**：检测攻击性、侮辱性内容
- **社交边界**：检查调侃强度、引战风险、用户情绪感知
- **事实检查**：防止编造不存在的事实
- **安全改写**：对不安全内容进行改写降级

### 4. BackgroundWorker (后台学习器)

负责异步处理学习任务：

- **画像更新**：提取用户偏好、群话题、活跃者
- **记忆提取**：使用 LLM 从对话中提取长期记忆
- **摘要压缩**：压缩历史对话以节省上下文空间
- **反思记录**：记录经验教训用于改进回复质量

### 5. SearchMCPClient (MCP 搜索客户端)

负责与 MCP 搜索工具的集成：

- **搜索意图判断**：识别用户是否需要搜索
- **URL 抽取**：从用户消息中提取 URL 并抽取内容
- **查询清洗**：清理搜索查询中的噪声
- **MCP 协议**：通过 stdio 与 search-engine-tool-mcp 通信

### 6. MemoryUtils (记忆辅助工具)

提供记忆处理的辅助函数：

- **噪音过滤**：过滤无意义的记忆内容
- **角色识别**：识别角色扮演内容防止写入事实
- **分数计算**：根据记忆类型计算重要性分数
- **去重合并**：合并相似的重复记忆

### 后台学习

响应发送后，`BackgroundWorker` 异步处理：

- 用户画像更新（偏好、关注点）
- 群画像更新（话题、活跃者）
- 聊天摘要压缩
- 记忆提取与评分
- 反思记录

## 关键特性

### 1. 群热度自适应

根据群聊活跃度动态调整回复策略：

| 状态 | 消息数/5分钟 | 回复条件 |
|------|-------------|---------|
| quiet | < 2 | @、回复、关键词、问题 |
| active | 2-5 | @、回复、关键词、强问题 |
| hot | 5-8 | 仅 @ 或回复 |
| flood | > 8 | 仅 @ 且短回复 |

**实现原理**：
- 实时统计群聊消息数
- 根据热度状态动态调整门控策略
- 避免在活跃群聊中过度刷屏

### 2. 多角色 LLM 路由

不同任务使用独立的 LLM 配置，优化性能和成本：

| 角色 | 用途 | 典型模型 |
|------|------|---------|
| chat | 日常聊天 | GPT-4o-mini |
| intent | 意图决策 | GPT-4o-mini |
| reason | 情感推理 | GPT-4o |
| vision | 图像理解 | GPT-4o |
| background | 后台学习 | GPT-4o-mini |
| long_context | 长上下文 | GPT-4o |

**优势**：
- 不同任务可选择不同模型
- 成本敏感任务可使用更便宜的模型
- 质量敏感任务可使用更强的模型

### 3. 社交边界安全

完整的社交边界控制系统，防止机器人不当言论：

**风险检测维度**：
- `attack`：直接攻击词汇（智障、傻逼等）
- `insult`：侮辱性表达
- `sarcasm_overdose`：过度阴阳怪气
- `flame_war`：拱火/引战
- `emotion_ignore`：对用户情绪不敏感
- `roleplay_attack`：角色扮演攻击

**调侃强度控制**：
- `none`：严格收敛，不玩梗
- `light`：只允许轻微接梗
- `normal`：自然轻松，不升级
- `high`：活跃但守边界

### 4. 记忆提取系统

智能记忆提取和管理：

**记忆类型及分数**：
- `boundary`：用户边界（0.85）
- `fact`：稳定事实（0.75）
- `preference`：偏好（0.70）
- `project_focus`：项目关注（0.60）
- `event`：事件（0.45）
- `style_signal`：风格信号（0.40）

**记忆处理流程**：
1. 噪音过滤（纯数字、填充词等）
2. 角色扮演识别
3. 分数计算
4. 去重合并
5. 冲突检测

### 5. MCP 搜索集成

通过 MCP 协议集成搜索功能：

**搜索触发条件**：
- 用户明确说"搜一下"、"查一下"
- 用户消息包含 URL + 抽取意图
- LLM 意图判断需要搜索
- 时效性问题（最新、今天）

**搜索流程**：
1. 搜索意图判断
2. 查询清洗
3. MCP stdio 调用
4. 结果注入上下文

## 配置说明

### 配置文件

项目使用多个配置文件分离不同层面的配置：

| 配置文件 | 说明 | 主要配置项 |
|---------|------|-----------|
| `config/bot.json` | 行为参数 | 冷却、热度阈值、超级用户、搜索配置 |
| `config/persona.json` | 人设配置 | 身份、性格、说话风格、示例对话 |
| `config/safety.json` | 安全边界 | 事实边界、降级回复、改写提示 |

### 环境变量

按角色分别配置 LLM：

```env
# 日常聊天模型
CHAT_OPENAI_BASE_URL=https://api.openai.com/v1
CHAT_OPENAI_API_KEY=sk-xxx
CHAT_OPENAI_MODEL=gpt-4o-mini

# 意图决策模型
INTENT_OPENAI_BASE_URL=https://api.openai.com/v1
INTENT_OPENAI_API_KEY=sk-xxx
INTENT_OPENAI_MODEL=gpt-4o-mini

# 情感推理模型
REASON_OPENAI_BASE_URL=https://api.openai.com/v1
REASON_OPENAI_API_KEY=sk-xxx
REASON_OPENAI_MODEL=gpt-4o

# 图像理解模型
VISION_OPENAI_BASE_URL=https://api.openai.com/v1
VISION_OPENAI_API_KEY=sk-xxx
VISION_OPENAI_MODEL=gpt-4o

# 后台学习模型
BACKGROUND_OPENAI_BASE_URL=https://api.openai.com/v1
BACKGROUND_OPENAI_API_KEY=sk-xxx
BACKGROUND_OPENAI_MODEL=gpt-4o-mini

# 长上下文模型
LONG_CONTEXT_OPENAI_BASE_URL=https://api.openai.com/v1
LONG_CONTEXT_OPENAI_API_KEY=sk-xxx
LONG_CONTEXT_OPENAI_MODEL=gpt-4o

# NapCat 配置
NAPCAT_HTTP_BASE_URL=http://127.0.0.1:3000
NAPCAT_ACCESS_TOKEN=your_token

# 搜索 MCP 配置
SEARCH_MCP_ENABLED=true
SEARCH_MCP_COMMAND=search-engine-tool-mcp
SEARXNG_BASE_URL=http://localhost:8080
```

可选地放一个项目根目录 `.env`，`run.py` 会自动加载。

## 运行指南

### 本地运行

终端模式（测试用）：

```bash
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.terminal.toml
```

NapCat 模式：

```bash
uv run python examples/yqy_bot/run.py --config examples/yqy_bot/config.onebot11.napcat.ws_reverse.toml
```

### Docker 运行

构建并启动：

```bash
cd examples/yqy_bot
docker compose up -d --build
docker compose logs -f qqbot
```

日常重启（代码更新后）：

```bash
docker compose restart qqbot
docker compose logs -f qqbot
```

**注意事项**：
- 代码以挂载方式进入容器，改完宿主机代码后直接重启容器即可生效
- NapCat 作为单独容器加入 `projects_qqbot-net`，机器人通过 `http://napcat:3000` 调用 NapCat HTTP API
- SearXNG 使用 host 网络监听 `8080`，机器人通过 `http://host.docker.internal:8080` 调用搜索后端
- 若本机没有 `host.docker.internal` 映射，请保留 compose 里的 `extra_hosts` 配置

## 项目结构

```text
src/yqy_bot/
├── __init__.py
├── plugins/
│   └── chat.py                 # ChatPlugin - iamai 插件入口
├── core/
│   ├── models.py               # 数据模型定义
│   ├── config.py               # 配置加载
│   ├── pipeline.py             # ChatPipeline - 消息处理管道
│   ├── llm_router.py           # LLMRouter - 多角色路由
│   └── cooldown.py             # 冷却和热度管理
├── intent/
│   └── router.py               # IntentRouter - 意图决策
├── response/
│   └── generator.py            # ResponseGenerator - 回复生成
├── context/
│   ├── builder.py              # ContextBuilder - 上下文组装
│   └── prompt.py               # PromptBuilder - 提示词构建
├── safety/
│   ├── guard.py                # SafetyGuard - 安全检查
│   └── social_safety.py        # SocialSafety - 社交边界控制
├── tools/
│   ├── search_mcp.py           # SearchMCPClient - MCP 搜索客户端
│   └── search_policy.py        # 搜索触发策略
├── qq/
│   ├── parser.py               # 消息解析
│   ├── sender.py               # 消息发送
│   ├── emoji.py                # EmojiService - 表情服务
│   ├── human.py                # HumanBehavior - 人类化行为
│   ├── napcat_client.py        # NapCat HTTP 客户端
│   └── napcat_tools.py         # NapCat 工具方法
├── storage/
│   ├── database.py             # 数据库连接
│   └── repositories.py         # 数据访问层
└── background/
    ├── worker.py               # BackgroundWorker - 后台学习
    └── memory_utils.py         # 记忆处理辅助工具
```

## 技术细节

### 导入方式

项目采用**绝对导入**（`from yqy_bot.xxx import ...`），而非相对导入（`from ..xxx import ...`）。

运行时需确保 `PYTHONPATH` 包含 `src` 目录：

- **本地运行**：iamai 的 TOML 配置中 `python_paths = ["src"]` 已自动设置
- **Docker 运行**：`docker-compose.yml` 中已配置 `PYTHONPATH`

### 数据库表结构

| 表名 | 存储内容 | 主要字段 |
|------|---------|---------|
| `chat_history` | 消息历史 | chat_key, role, content, user_id, metadata |
| `chat_summary` | 对话摘要 | chat_key, summary, updated_at |
| `user_profile` | 用户画像 | user_id, stable_facts, preferences, boundaries |
| `group_profile` | 群画像 | group_id, group_style, common_topics, active_members |
| `memory` | 提取的记忆 | user_id, group_id, kind, content, score |
| `reflection` | 反思记录 | chat_key, content, created_at |

### 依赖关系

```
run.py
  └── iamai Runtime
      └── ChatPlugin
          └── ChatPipeline
              ├── ContextBuilder
              ├── IntentRouter
              ├── ResponseGenerator
              ├── SafetyGuard
              │   └── SocialSafety
              ├── EmojiService
              ├── HumanBehavior
              ├── SearchMCPClient (可选)
              └── BackgroundWorker
                  └── MemoryUtils
```

## 架构文档

详细架构说明见 [docs/architecture.md](docs/architecture.md)。

## 许可证

MIT License
