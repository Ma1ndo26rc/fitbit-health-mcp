# Fitbit Health Project Handoff

## 1. Project Snapshot

- **项目路径：** `E:\CodeX_Lab`
- **当前 Git 分支：** `feat/fitbit-health-pipeline`
- **当前 HEAD：** `905548dd974087699342935699742e8d314a0c60` (`905548d docs: document Fitbit fetch tiers`)
- **工作区状态：** 创建本文档前为干净状态；创建后预期只有未提交的 `docs/CODEX_HANDOFF.md`。
- **当前测试结果：** `D:\anaconda\python.exe -m pytest -q`，结果为 `97 passed in 5.93s`。
- **Python：** Python 3.12.3，解释器为 `D:\anaconda\python.exe`；项目以 editable 方式解析到 `E:\CodeX_Lab\src\fitbit_health`。
- **CLI 运行方式：** `D:\anaconda\python.exe -m fitbit_health sync --days 7`
- **MCP 运行方式：** `D:\anaconda\python.exe -m fitbit_health.mcp_server`，也可运行 `D:\anaconda\Scripts\fitbit-health-mcp.exe`。
- **Codex MCP 配置位置：** `C:\Users\SJC\.codex\config.toml`。
- **Codex MCP 注册：** 名称 `fitbit-health`，stdio command 为 `D:\anaconda\python.exe`，args 为 `-m fitbit_health.mcp_server`，cwd 为 `E:\CodeX_Lab`，状态为 enabled。若 PowerShell 的 `HOME` 被其他软件改写，先设置 `$env:CODEX_HOME = 'C:\Users\SJC\.codex'` 再运行 `codex mcp list`。

主要入口：

- `src/fitbit_health/__main__.py`：CLI 参数解析与命令入口。
- `src/fitbit_health/pipeline.py`：本地同步、标准化、分析和报告编排。
- `src/fitbit_health/mcp_server.py`：六个 stdio MCP tools 的注册与启动。
- `src/fitbit_health/mcp_tools.py`：MCP 调用的安全 JSON envelope 和数据编排。

## 2. Current Product Scope

当前项目是：

> **本地单用户 Fitbit / Google Health 数据管线 + CLI + stdio MCP Server**

当前项目不是：

- Web 应用；
- 多用户服务；
- 公网 HTTP MCP；
- 可直接公开部署的生产服务。

项目当前以本机文件、本机 Desktop OAuth 和本机进程为边界。不要将 stdio MCP 或本地 token 文件直接暴露到公网。

## 3. Current Architecture

```text
CLI / stdio MCP
        ↓
Google Desktop OAuth
        ↓
Google Health API
        ↓
normalize
        ↓
analytics
        ↓
JSON / Markdown / MCP structured output
```

核心文件与职责：

| 文件 | 职责 |
| --- | --- |
| `src/fitbit_health/config.py` | Google Health scopes 与 Desktop OAuth credential 发现。 |
| `src/fitbit_health/auth.py` | Desktop OAuth、token 加载、刷新与本地保存。 |
| `src/fitbit_health/client.py` | Google Health REST 请求、分页、超时、有限重试与安全错误映射。 |
| `src/fitbit_health/fetch_window.py` | 统一的 14/7/3/1 天请求窗口规则。 |
| `src/fitbit_health/normalize.py` | 将五类 Google Health 数据标准化为按日 schema。 |
| `src/fitbit_health/analytics.py` | 指标均值、基线和睡眠规律性计算。 |
| `src/fitbit_health/report.py` | 写入 JSON 与中文 Markdown 报告。 |
| `src/fitbit_health/pipeline.py` | CLI 使用的完整同步管线。 |
| `src/fitbit_health/mcp_tools.py` | MCP 使用的数据获取、分析和结构化诊断。 |
| `src/fitbit_health/mcp_server.py` | stdio MCP Server 和六个 tool 定义。 |

CLI 输出固定为：

- `reports/daily_health_summary.json`
- `reports/health_analysis.json`
- `reports/health_report.md`

MCP 输出固定包含：

- `requested_days`
- `available_days`
- `data`
- `missing_data`
- `diagnostics`

## 4. Confirmed Fetch-Window Design

- **允许请求天数：** `14、7、3、1`
- **默认请求天数：** `7`

这是刻意的产品设计，不是临时错误。原因：

- 30 天请求数据量过大；
- 请求耗时过长；
- 部分 Google Health 数据接口不适合单次请求超过 14 天；
- 当前版本不应该恢复单次 30 天请求。

后续统计应描述为“请求窗口统计”，不应继续使用误导性的 `thirty_day_mean` 或“30 天平均值”。如果未来需要真实 30 天趋势，应通过增量历史存储逐日积累，再在历史数据上计算，而不是恢复单次 30 天抓取。

**增量历史存储属于未来方向，当前不实施。**

## 5. Existing Capabilities

仓库中已经实现并验证：

- Google Desktop OAuth；
- 本地 token 复用与刷新；
- Google Health API 只读请求；
- 分页、30 秒超时和有限指数退避重试；
- 睡眠、步数、平均心率、静息心率、HRV 五类数据；
- 按日数据标准化和诊断保留；
- 指标分析与睡眠规律性输出；
- CLI 同步和 JSON/Markdown 输出；
- stdio MCP initialize/list_tools/call_tool；
- 六个 MCP tools：
  - `get_sleep`
  - `get_steps`
  - `get_heart_rate`
  - `get_resting_heart_rate`
  - `get_hrv`
  - `get_health_summary`
- MCP tool schema 将 `days` 显示为 `[14, 7, 3, 1]`，默认 7；
- MCP 非法参数、认证失败、单类 API 失败和内部异常的结构化诊断；
- token、refresh token、client secret 和 Authorization header 的输出防泄漏测试；
- 请求窗口统计已使用 `window_mean` / `window_samples`，基于传入窗口中的全部有效数据计算，不再保留 `thirty_day_mean` / `thirty_day_samples`；中文报告动态显示“近 N 天均值”，14、7、3、1 天请求正常，30 天请求被拒绝；
- 97 个自动化测试，覆盖 auth、client、normalize、analytics、pipeline、CLI、MCP service、MCP schema 和 stdio 握手。

本会话曾使用真实 Google Health 账号验证 MCP 汇总调用成功；交接文档不记录任何私人健康数值或凭据。自动化测试不依赖真实 OAuth 或外部网络。

## 6. Known Issues

以下问题已确认，但本次交接不修复。

### P0

#### 6.1 Windows token 和报告目录 ACL 不是真正的访问控制

- **当前现象：** `auth.py` 的 Windows 分支主要调整 Hidden/ReadOnly 文件属性，没有将 `.private/token.json`、`.private/` 和 `reports/` 的 DACL 收紧为仅当前用户可访问。当前 ACL 元数据表明其他本地用户组可能具有读取或修改能力。
- **风险：** OAuth refresh material 和私人健康报告可能被同一台电脑上的其他账户读取或篡改；`.gitignore` 只能防止提交，不能提供本地访问控制。
- **未来处理：** 在写入前创建 owner-only 目录并设置显式 Windows ACL；对 token 和报告分别添加 ACL 回归测试。Web 化后改用数据库密文和 KMS/Secret Manager，不继续依赖本地文件权限。

#### 6.3 `minutes_asleep` 为空时睡眠 availability 可能错误

- **当前现象：** 如果 Google 返回有效睡眠 interval 但 summary 中缺少 `minutesAsleep`，标准化层仍可能生成 sleep 字典；MCP 和分析层可能因此把该日计为 available。
- **风险：** 返回 `available_days > 0`，但主要睡眠字段为 `null`，同时 `missing_data` 不包含该日期。
- **未来处理：** 以 `minutes_asleep is not None` 作为主要睡眠可用性判断，并对 partial/unprocessed sleep payload 增加 normalize、MCP 和 summary 回归测试。

### P1

#### 6.4 CLI 与 MCP 抓取编排重复

- **当前现象：** `pipeline.run_sync()` 与 `HealthMCPService.get_health_summary()` 都独立抓取五类数据、标准化并分析。
- **风险：** 修复和行为变更需要在两个入口同步，容易出现偏差。
- **未来处理：** 抽出传输无关的 `HealthSyncService`，由 CLI、MCP 和未来 Web adapter 复用。

#### 6.5 缺少统一 `HealthSyncService`

- **当前现象：** 认证、抓取、标准化、分析和输出边界主要由入口函数组合，没有明确 application service。
- **风险：** Web 化或增加新 adapter 时容易复制业务逻辑。
- **未来处理：** 定义一个只返回 typed snapshot 的 application service；文件写入、MCP envelope 和 HTTP response 保持为独立 adapter。

#### 6.6 跨模块仍传递松散字典

- **当前现象：** normalize、analytics、report 和 MCP 之间主要传递嵌套 `dict`。
- **风险：** 字段改名或空值语义变化只能在运行时发现。
- **未来处理：** 在不扩大当前任务范围的前提下，逐步引入明确 DTO/Pydantic model 和 schema version 迁移策略。

#### 6.7 用户时区处理不完整

- **当前现象：** 请求窗口结束日期依赖运行机器的 `date.today()`，没有持久的用户时区配置。
- **风险：** 部署到不同时区的服务器后，日期边界和睡眠归属可能变化。
- **未来处理：** 将用户 IANA timezone 作为显式输入，统一由 application service 计算日期窗口。

#### 6.8 401 刷新重试和 refresh token 并发锁可改进

- **当前现象：** 启动调用前可以刷新过期 token，但请求过程中遇到 401 时没有统一 refresh-and-replay；多个并发调用也没有 refresh 锁。
- **风险：** Web/多进程环境可能出现偶发失败、重复刷新或 token 文件写入竞争。
- **未来处理：** 在 credential provider 中实现一次性 401 refresh/replay，并为每用户 refresh 增加锁和幂等更新。

#### 6.9 多 sleep session 选择规则可改进

- **当前现象：** 当前代码尝试读取 `metadata.main`，否则倾向选择最长 session；Google 当前 payload 更可能使用 nap 语义。
- **风险：** 同一天存在主睡眠和 nap 时可能选错 session。
- **未来处理：** 增加多 session fixture，优先选择明确的非 nap session，再以持续时间作为后备规则。

## 7. Deferred Architecture

以下内容只是未来方向，本阶段不要自动实施：

- FastAPI Web API；
- Google Web OAuth；
- PostgreSQL；
- 加密 refresh token；
- Redis / Worker；
- Streamable HTTP MCP；
- 多用户系统；
- KMS / Secret Manager；
- 生产级审计与监控。

**只有在用户明确开启 Web MVP 或生产化任务时，才允许设计或实施这些内容。** 当前任务或局部 bugfix 不得以“为未来做准备”为由引入上述架构。

## 8. Recommended Next Tasks

按最小独立任务排序；每项应单独设计、单独测试、单独提交：

1. **清理错误的 30 天统计字段和文案。** 仅修改 analytics/report/README 及直接测试，将语义改为请求窗口统计，不恢复 30 天抓取。
2. **修复睡眠 availability。** 以 `minutes_asleep` 为主要值，补 partial sleep payload 回归测试。
3. **修复 Windows ACL。** 仅处理 `.private`、token 和 reports 权限及权限测试，不改变 OAuth 流程。
4. **抽取 `HealthSyncService`。** 保持 CLI/MCP 对外行为不变，消除两条编排路径的重复。
5. **增加明确用户时区。** 先为 application service 增加 timezone 输入及日期边界测试，不引入数据库或多用户模型。
6. **再考虑 Web OAuth 原型。** 只有用户明确开启 Web MVP 后，才新增 Web OAuth callback、后端 session 和 Web API。

## 9. Working Rules for Future Codex Sessions

- 一次只处理一个明确任务。
- 不根据 roadmap 自动实现未来功能。
- 不顺便重构。
- 不为了“完整性”扩大修改范围。
- 不恢复 30 天单次抓取。
- 不新增数据库、Web API、worker 或新架构，除非用户明确要求。
- 优先修改最少文件。
- 修改前先检查现状。
- 测试失败时只修复与当前任务直接相关的问题。
- 达到停止条件后立即停止。
- 不反复运行同一类命令。
- 如果连续两次修复未推动测试结果，应停止并报告阻塞原因。
- 不在没有新证据时重复分析。
- 不进行超过当前任务范围的“进一步优化”。
- 不输出 access token、refresh token、OAuth client secret、API key、Authorization header 或私人健康数据。
- 除非用户明确要求，不合并分支、不创建 PR、不部署。

## 10. New Session Bootstrap Prompt

复制以下提示词到新的 Codex 会话：

```text
你正在继续 E:\CodeX_Lab 的 Fitbit Health 项目。

开始前必须：
1. 完整读取 docs/CODEX_HANDOFF.md，并将它作为本会话的主要项目上下文；
2. 实际检查 Git 分支、HEAD、git status --short 和当前测试结果；
3. 不自动执行交接文档中的 roadmap、Deferred Architecture 或 Recommended Next Tasks；
4. 完成检查后等待我给出一个具体任务；
5. 每次只处理一个明确任务，并严格限制修改范围；
6. 不恢复单次 30 天抓取，不顺便重构，不新增 Web API、数据库、worker、多用户系统或远程 MCP，除非我明确要求；
7. 测试失败时只处理与当前任务直接相关的问题；连续两次修复未推动结果时，停止并报告阻塞；
8. 达到当前任务的停止条件后立即结束，不继续提出或实施额外优化；
9. 不输出任何 token、OAuth secret、API key、Authorization header 或私人健康数据。

现在先执行第 1 和第 2 项，只报告检查结果，然后等待我的具体任务。
```
