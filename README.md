# Codex 用量面板

[版本发布](https://github.com/dabogeeee/codex-usage-monitor/releases) · [v0.1.0 网页版](https://github.com/dabogeeee/codex-usage-monitor/releases/tag/v0.1.0) · [v0.2.0 原生版](https://github.com/dabogeeee/codex-usage-monitor/releases/tag/v0.2.0)

本机 Codex 插件，配套 SwiftUI/AppKit 原生悬浮面板和菜单栏入口。显示每日、每周、每月、累计 token 用量，账号剩余额度，以及每个聊天的缓存命中率和上下文占用估算。

## 版本

| 版本 | 界面与用途 | 状态 |
|---|---|---|
| v0.1.0 | 本机网页版，可在浏览器或聊天旁打开 | 历史版本，见独立发布页 |
| v0.2.0 | SwiftUI/AppKit 原生悬浮面板、菜单栏余额、自动打开和主题选择 | 已发布版本 |
| v0.3.0 | 菜单栏仅显示余额，展开查看用量详情 | 本地测试版本 |

普通用户请从 Releases 下载带已编译应用的安装包。仓库源码不跟踪生成的 `.app` 和缓存；从源码构建需要 Swift Command Line Tools。两个版本各自的说明和安装包位于对应 Release，历史源码由版本标签保留。

## 使用

**打开 Codex 时，原生面板会自动显示。** 关闭面板只会收起窗口，菜单栏继续显示余额；点击菜单栏图标可重新打开。退出 Codex 后，面板隐藏、统计服务停止，后台只保留启动监听。

也可以双击 `启动用量面板.command`，或打开 `~/Applications/CodexUsage.app`。插件已安装时，也能在新聊天中输入：

> 打开 Codex 用量面板

面板提供“跟随系统／浅色／深色”主题，窗口标题栏同步切换，并保存用户选择。可以直接在面板顶部或菜单栏的“主题”菜单选择；也可切换完整／精简视图和置顶状态。

菜单栏顶部仅显示**剩余额度**，例如 `5h 80% · W 20%`，减少占用空间。“用量与所选聊天”菜单列出所选聊天 UUID、缓存命中率、上下文占用、今日／本周／本月／累计 tokens 和剩余额度。点击“复制所选聊天 UUID”可复制标识；切换尚未完成或没有对应日志时，聊天指标显示 `—`。

原生界面不使用浏览器或 WebView，不启动 HTTP 服务。统计通过本机进程管道传递，使用 Python 标准库读取数据，不需要持续打开终端。

双击 `安装插件.command` 可安装插件、原生应用和自动启动项；只安装原生面板与自动打开功能，可以运行 `安装原生面板并启用自动打开.command`。支持 Apple Silicon Mac、macOS 14+、Python 3.9+ 和已登录的 Codex CLI。Release 安装包包含已编译应用，不需要 Swift 编译器来运行；首次登录应由你在 Codex 中完成。

自动启动项位于 `~/Library/LaunchAgents/com.codexusage.monitor.plist`。它在用户登录后运行一个轻量监听进程，通过 macOS `NSWorkspace` 检测 Codex 的启动与退出；不依赖需要单独信任的 Codex 生命周期 hooks。当前机器已确认 Codex 的 bundle identifier 为 `com.openai.codex`。安装时 Codex 已在运行，也会立即显示面板。

停用自动打开：双击 `停用自动打开.command`。它会卸载本插件的启动项，保留原生应用和源码，可随时手动打开或重新启用。无需关闭当前 Codex 来完成安装。

## 指标与范围

| 指标 | 数据来源与含义 |
|---|---|
| 本机今日／本周／本月／累计 tokens | 只读 `CODEX_HOME`（默认 `~/.codex`）中的活动与归档 JSONL 日志。含子代理。按 `Asia/Shanghai` 日期统计；本周从周一、本月从一日开始。累计指可读取日志中的全部日期。 |
| 账号官方 tokens | `account/usage/read` 返回的账号 token 活动数据。累计取官方 `lifetimeTokens`，周／月为服务返回日桶之和。服务未声明日桶时区及完整历史覆盖范围，因此周／月可能不完整。 |
| 剩余额度 | `account/rateLimits/read` 返回的每个窗口：`100 - usedPercent`。显示百分比与官方重置时间，不折算为剩余 tokens。 |
| 最近请求缓存命中率 | `cached_input_tokens / input_tokens × 100%`。累计缓存命中率按累计输入 token 加权。缓存输入已经属于输入总量，不重复相加。 |
| 最近请求上下文占用估算 | 日志最近请求的 `last_token_usage.total_tokens / model_context_window × 100%`。使用日志中的有效上限，不硬编码模型窗口。生成过程中与压缩后可能和原生界面显示不同。 |

本机数据每 10 秒读取一次；官方账号额度与统计每 60 秒请求一次，最低可配置到 15 秒。后台请求不启动模型会话，不为刷新产生模型 tokens。官方数据存在延迟：本机验证时账号日桶最新日期为 **2026-09-29**，不是 9 月 30 日。因此当天缺失显示 `—`，不显示误导性的 `0`；每个来源都明确标注覆盖范围。缺失指标也显示 `—`。

本机 token 统计与账号官方汇总不能直接视为同一口径：其他设备、云端活动、已删除或不完整日志可能影响差异。日志只保留后半段时，首次继承计数只计算最近一次可观测请求；更早的未保存记录无法恢复。本机累计不会冒充账号全历史。

Plus 默认显示实际返回的 5 小时与一周额度。Pro／Pro Lite／Pro Max 默认显示一周额度，其他返回窗口可展开。其他套餐按服务返回的窗口显示；API Key 登录没有订阅额度百分比。

## 切换聊天

可以在面板按项目及聊天名称手动选择，或选择“跟随最近有活动的聊天”。这个模式跟随最新产生 token 记录的普通聊天，排除自动审批代理等子代理作为默认选中对象。

本版不能直接读取桌面端当前选中的聊天 UUID。“最近活动”依据本机用量日志，切换到没有新请求的聊天时，请在面板手动选择。展开菜单后可查看“最近活动”或“手动选择”模式。本版不读取桌面界面，无需辅助功能权限。

本版是独立原生悬浮窗口，可以保持在 Codex 旁边。它不是对 Codex 主窗口固定侧栏的注入，也不修改 Codex 应用文件。

## 运行与开发

```sh
python3 scripts/build_native.py
python3 scripts/install_native.py
python3 plugins/codex-usage-monitor/native_mcp.py --native
python3 plugins/codex-usage-monitor/native_mcp.py --snapshot
python3 plugins/codex-usage-monitor/native_mcp.py --mcp
python3 -m unittest discover -s tests -v
```

可以指定 `--timezone Asia/Shanghai`、`--refresh-seconds 60`、`--codex-home /path/to/codex-data`。这些参数不会覆盖系统环境变量。CLI 不在 PATH 时，可以通过 `CODEX_USAGE_CLI` 指定其路径；Mac 上会尝试自动找到 ChatGPT/Codex 应用自带的 CLI。

Codex 自身需要能够读写其本地状态目录，才能启动 app-server。插件不直接读取或输出登录凭据，也不会替你登录、退出、充值、兑换额度重置或启动模型任务。只读客户端仅允许初始化和三个账号读取方法。

数据展示在本机；原生面板不加载 CDN、分析脚本或远程字体。官方用量查询由已登录的 Codex 发起。缓存账号数据仅保存在服务内存中。聊天名称来自只读 SQLite 元数据，不输出会话正文。主题、精简视图和置顶偏好由本机用户设置保存。请勿公开包含私人聊天名称的截图或真实账号统计。

插件安装位置由 Codex 管理，本版为 `~/.codex/plugins/cache/codex-usage-local/codex-usage-monitor/0.3.0`。原生应用安装在 `~/Applications/CodexUsage.app`。更新源代码后重新构建并运行安装脚本；它只替换本插件的应用和启动项。卸载前先运行 `停用自动打开.command`，然后：

```sh
codex plugin remove codex-usage-monitor@codex-usage-local
codex plugin marketplace remove codex-usage-local
```

后端测试覆盖累计差分、续聊继承、跨文件重发、分叉及归档去重、时区边界、不完整行、文件截断、数据缺失、Plus/Pro 规则、MCP 和原生进程管道。原生应用检查包含真实账号读取、窗口显示、启动／退出状态转换、关闭后重开，以及深色／浅色主题渲染。v0.2.0 已移除旧网页服务、HTML/CSS/JavaScript 资源和相关浏览器检查脚本。网页版仅保留在 v0.1.0 版本中。

## 官方依据

- [Codex App Server：账号用量与额度接口](https://learn.chatgpt.com/docs/app-server)
- [插件包、MCP 与本地市场格式](https://developers.openai.com/plugins/build/plugins)
