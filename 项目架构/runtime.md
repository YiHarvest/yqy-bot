# runtime.py - 运行时容器

## 概述

`runtime.py` 是 iamai 的**核心调度器**，负责整个框架的生命周期管理。它是一个顶层容器，统一管理：适配器加载、插件系统和事件分发。

**文件位置**：[`python/iamai/runtime.py`](../python/iamai/runtime.py)

## 核心类 `Runtime`

### 职责

```
配置文件 (config.toml)
       │
       ▼
  Runtime 容器
       │
       ├── load_plugins()      // 加载并实例化所有插件
       ├── load_adapters()     // 加载并实例化所有适配器
       │
       ├── bootstrap()         // 启动：插件 startup() 钩子
       │
       └── serve()             // 运行事件循环
            │
            ├── start_adapters()   → 适配器开始接收外部事件
            ├── dispatch(event)    → 匹配 handler → 执行中间件管道
            └── shutdown()         → 优雅关闭
```

### 关键方法

| 方法 | 说明 |
|------|------|
| `from_config_file(path)` | 从 TOML 文件创建 Runtime 实例 |
| `bootstrap()` | 加载配置 → 插件 → 适配器 → 执行 startup 钩子 |
| `serve()` | 启动适配器，进入事件循环，直到停止信号 |
| `dispatch(event, adapter)` | 将事件分发给匹配的 handler |
| `load_plugins()` | 从配置的 plugin_dirs 和 entry_points 加载插件 |
| `reload_plugins()` | 热重载（开发时修改插件代码自动生效） |
| `shutdown()` | 停止适配器 → 取消 handler 任务 → 执行 shutdown 钩子 |
| `register_dependency(name, value)` | 注册依赖注入项 |

### 事件分发流程

```
dispatch(event) 
    │
    ▼
收集所有插件的匹配 handler
    │
    ▼
按 priority 排序，检查 rule 和 permission
    │
    ▼
构建 Context(ctx) 
    │
    ▼
_execute_handler_job → 中间件管道
    │
    ├── before 中间件  (record_source, 日志等)
    ├── around 中间件  (调用链包装)
    ├── handler 回调   (业务逻辑)
    ├── after 中间件   (后处理)
    └── error 中间件   (异常处理)
```

### 热重载

```
_watch_plugin_changes()
    │
    ▼
检测到 .py 文件变化
    │
    ▼
reload_plugins()
    │
    ├── 保存旧插件状态
    ├── 重新加载模块
    ├── 执行 startup() 钩子
    ├── 替换内部插件列表
    └── 执行旧插件 shutdown() 钩子
```

## 内置适配器

```python
BUILTIN_ADAPTERS = {
    "terminal":  "iamai.adapters.terminal:TerminalAdapter",
    "onebot11":  "iamai.adapters.onebot11:OneBot11Adapter",
    "telegram":  "iamai.adapters.telegram:TelegramAdapter",
    "webhook":   "iamai.adapters.webhook:WebhookAdapter",
}
```

## 内置插件

```python
BUILTIN_PLUGINS = {
    "management":      "iamai.plugins.management:ManagementPlugin",
    "management_api":  "iamai.plugins.management_api:ManagementApiPlugin",
}
```
