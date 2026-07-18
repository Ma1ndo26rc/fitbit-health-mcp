# Fitbit Health 本地数据管线设计

## 目标

构建一个仅在本机运行的 Python 工具，通过 Google Health API 读取用户的 Fitbit Air 健康数据，生成最近 30 天的标准化数据、7 天与 30 天趋势指标，以及中文 Markdown 报告。

本阶段不包含 MCP、Web Dashboard、定时云任务或医学诊断功能。

## 成功标准

- 使用现有 Google Cloud 项目 `my-fitbit-air-502810` 的桌面 OAuth 客户端完成登录。
- OAuth 通过本地 loopback 回调完成，不要求用户复制授权码或手动打开 localhost 地址。
- 使用 refresh token 在后续运行中自动更新 access token。
- 以只读权限获取最近 30 天可用的睡眠、步数、心率、静息心率和 HRV 数据。
- 在部分数据类型缺失、某日未佩戴设备或 API 返回空集合时仍能生成报告，并明确标记缺失数据。
- 输出标准化 JSON、分析 JSON 和中文 Markdown 报告。
- 凭据、token、原始健康数据和生成的个人报告不纳入 Git。
- 解析、标准化、趋势计算和报告渲染具有自动化测试。

## 选定方案

使用 Google OAuth 桌面应用流程和临时本地 HTTP 回调服务器。程序运行时启动 loopback 监听器并打开 Google 授权页；授权完成后由 Google 重定向至本地监听器。固定的 Web OAuth 客户端和 OAuth Playground 不参与日常运行。

选择该方案的原因：

- 与本地 CLI 的部署模型一致。
- 不需要固定端口或人工复制 token。
- 能安全地获得并刷新长期访问凭据。
- 后续可以在不更换数据层的情况下由 MCP 调用。

## 架构

### OAuth 层

- 自动识别工作区中的桌面应用凭据文件。
- 首次运行时启动 loopback 回调并打开系统浏览器。
- 请求以下只读 scopes：
  - `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
  - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
  - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
- 将授权结果写入本地 token 文件。
- token 过期时自动刷新；刷新失败时提示重新授权，不回显敏感值。

### Google Health 客户端

- 统一封装 REST 请求、分页、超时和错误映射。
- 使用 `users/me` 和 reconciled data stream，优先获得与 Google Health 应用一致的数据。
- 第一阶段读取：
  - `sleep`
  - `steps`
  - `heart-rate`
  - `daily-resting-heart-rate`
  - `daily-heart-rate-variability`
- 所有请求均为只读。
- 单个非关键数据类型失败时记录诊断信息并继续；身份验证失败或全部请求失败时终止运行。

### 标准化层

- 将 API 响应映射到按本地日期组织的每日记录。
- 保留原始数值和单位，不伪造缺失值。
- 每日记录包含数据可用性标记和来源诊断。
- 睡眠跨越午夜时，按睡眠会话结束日期归属。
- 输出稳定、版本化的 JSON schema，供后续 MCP 复用。

### 分析层

- 计算最近 7 天和 30 天的有效样本均值。
- 计算最近 7 天相对前序基线的绝对变化和百分比变化。
- 睡眠分析包含时长和入睡/起床时间规律性；数据不足时不输出趋势判断。
- 静息心率与 HRV 分开评估，不合成为未经验证的医学或恢复评分。
- 所有结论必须能够追溯到输入指标，并附样本天数。

### 报告层

生成两个主要文件：

- `daily_health_summary.json`：标准化每日数据与诊断信息。
- `health_report.md`：中文摘要、趋势、数据质量和非医疗性质说明。

另生成 `health_analysis.json`，保存报告使用的结构化统计指标，避免下游程序从 Markdown 反向解析。

## 命令行接口

第一阶段提供一个主入口：

```text
python -m fitbit_health sync --days 30
```

行为：完成授权或 token 刷新、抓取数据、标准化、分析并写出报告。失败时返回非零退出码，并给出不含敏感信息的可操作错误。

## 文件与隐私边界

- OAuth 凭据文件保留用户下载时的原文件，不在程序中复制其内容。
- `.gitignore` 排除 `client_secret_*.json`、token 文件、`data/raw/`、`data/processed/` 和生成报告。
- 日志不得包含 Authorization header、access token、refresh token、client secret 或完整原始响应。
- 测试仅使用合成夹具，不使用真实健康数据。
- 本项目只提供数据趋势与一般健康信息，不进行诊断、治疗或用药建议。

## 错误处理

- 无桌面凭据：列出正确的下载与放置方式。
- 同时存在桌面和 Web 凭据：明确选择桌面凭据。
- 用户取消授权：安全退出并保留可重试说明。
- refresh token 失效：删除或隔离失效会话前先提示重新授权，不删除 OAuth 客户端凭据。
- API 429 或暂时性 5xx：进行有上限的指数退避重试。
- 单项权限不足：在诊断中标记对应数据类型，其他数据继续处理。
- 空数据：生成带数据质量提示的空报告，不制造趋势。

## 测试策略

- OAuth 凭据发现：桌面、Web、缺失和多文件情形。
- API 客户端：分页、空响应、权限错误、重试和响应结构校验。
- 标准化：跨午夜睡眠、缺失日、单位和日期边界。
- 分析：7 天/30 天窗口、样本不足、零基线和百分比变化。
- 报告：中文输出、样本量、缺失数据说明和免责声明。
- 端到端测试：使用合成 API 响应运行完整本地管线。
- 实际验证：用户完成 OAuth 后运行一次只读同步，检查 HTTP 状态、输出 schema 和敏感信息泄漏。

## 后续阶段

MCP 阶段将复用 Google Health 客户端、标准化 schema 和分析层，只新增受控工具接口，例如 `get_daily_health`、`get_sleep_summary` 和 `get_recovery_trend`。本设计不提前实现这些接口。
