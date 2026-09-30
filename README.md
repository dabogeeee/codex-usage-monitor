# Codex 用量面板

本机 Codex 插件，显示每日、每周、每月、累计 token 用量，账号剩余额度，以及每个聊天的缓存命中率和上下文占用估算。

## 使用

插件已安装时，在**新聊天**中输入：

> 打开 Codex 用量面板

支持 MCP Apps 的客户端可以展示插件的会话面板入口；本机 Codex 也可以把工具返回的地址用 `open_in_codex` 在右侧浏览器面板打开。原生入口是否展示取决于客户端支持，不能保证它出现在每个 Codex 版本中。浏览器面板已在本机验证。

也可以双击本目录的 `启动用量面板.command`，在默认浏览器打开。保持终端运行，关闭服务用 Ctrl+C。浏览器面板只监听 `127.0.0.1`。重启服务后请使用新生成的完整地址，旧的访问密钥会失效。

如果要在另一台 Mac 安装，双击 `安装插件.command`。该脚本通过 Codex CLI 添加这个本地插件来源，并安装 `codex-usage-monitor@codex-usage-local`。新建聊天后工具才会加载；必要时重新打开 Codex。无需 OpenAI API Key，也无需 pip/npm 安装运行依赖。需要 Python 3.9+ 和已登录的 Codex CLI；首次登录应由你在 Codex 中完成。

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

**这个插件不能读取桌面端当前选中的聊天 UUID。** 在 Codex 切换到一个没有新请求的聊天，并不保证“最近活动”立即跟随。因此它明确显示“最近活动”或“手动选择”，不会把其他聊天的指标标成当前选中聊天。需要精确查看时，请在面板选择那个聊天。

本版提供可打开的会话面板，不向 Codex 原生固定侧栏或标题栏注入数字，也不修改 Codex 应用文件。

## 运行与开发

```sh
python3 plugins/codex-usage-monitor/server.py --serve
python3 plugins/codex-usage-monitor/server.py --snapshot
python3 plugins/codex-usage-monitor/server.py --mcp
python3 -m unittest discover -s tests -v
```

可以指定 `--timezone Asia/Shanghai`、`--port 8766`、`--refresh-seconds 60`、`--codex-home /path/to/codex-data`。这些参数不会覆盖系统环境变量。CLI 不在 PATH 时，可以通过 `CODEX_USAGE_CLI` 指定其路径；Mac 上会尝试自动找到 ChatGPT/Codex 应用自带的 CLI。

Codex 自身需要能够读写其本地状态目录，才能启动 app-server。插件不直接读取或输出登录凭据，也不会替你登录、退出、充值、兑换额度重置或启动模型任务。只读客户端仅允许初始化和三个账号读取方法。

数据展示在本机；面板不加载 CDN、分析脚本或远程字体。官方用量查询由已登录的 Codex 发起。缓存账号数据仅保存在服务内存中。聊天名称来自只读 SQLite 元数据，不输出会话正文。浏览器 API 使用随机临时访问密钥，密钥放在 URL fragment 中，不进入 HTTP 请求日志；API 同时检查 Host 和 Origin。不要将面板地址或真实统计发布到公开网站。

插件安装位置由 Codex 管理。本机测试的安装位置为 `~/.codex/plugins/cache/codex-usage-local/codex-usage-monitor/0.1.0`。更新源代码后重新运行安装脚本，并在新聊天加载；已打开的旧服务需要停止并重启。卸载插件：

```sh
codex plugin remove codex-usage-monitor@codex-usage-local
codex plugin marketplace remove codex-usage-local
```

后端测试覆盖累计差分、续聊继承、跨文件重发、分叉及归档去重、时区边界、不完整行、文件截断、数据缺失、Plus/Pro 规则、MCP 资源及本机接口认证。浏览器检查包含 320/420 px 布局、深色模式、统计来源切换和聊天选择。MCP Apps 原生入口进行了协议及模拟宿主验证；实际入口可用性仍需在支持该扩展的客户端确认。

## 官方依据

- [Codex App Server：账号用量与额度接口](https://learn.chatgpt.com/docs/app-server)
- [插件包、MCP 与本地市场格式](https://developers.openai.com/plugins/build/plugins)
- [插件会话面板入口扩展](https://developers.openai.com/plugins/build/extensions)
- [MCP Apps UI 与工具桥接](https://developers.openai.com/plugins/build/chatgpt-ui)
