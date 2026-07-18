# Fitbit Health 本地 MCP Server 设计

## 目标

在现有 Fitbit Health 本地数据管线上增加一个供 ChatGPT/Codex 调用的 stdio MCP Server。Server 使用官方 Python MCP SDK，暴露六个只读健康数据工具，并复用已有 OAuth、Google Health API client、标准化与分析代码。

本阶段不包含 Web Server、Streamable HTTP、SSE、Cloudflare、远程部署、Dashboard 或报告渲染改造。

## 成功标准

- 使用官方 Python MCP SDK 稳定版，依赖固定为 `mcp>=1.27,<2`。
- `fitbit-health-mcp` 和 `python -m fitbit_health.mcp_server` 均以 stdio 启动。
- MCP `initialize` 与 `list_tools` 握手成功。
- 六个工具均可见：
  - `get_sleep(days: int = 7)`
  - `get_steps(days: int = 7)`
  - `get_heart_rate(days: int = 7)`
  - `get_resting_heart_rate(days: int = 7)`
  - `get_hrv(days: int = 7)`
  - `get_health_summary(days: int = 7)`
- 每个工具返回结构化 JSON 对象，不返回 Markdown。
- 自动化测试不发起真实 Google OAuth 或 Google Health API 请求。
- 现有健康报告逻辑保持不变。

## SDK 与传输

使用官方 `mcp` Python 包中的 `mcp.server.fastmcp.FastMCP`，版本范围 `>=1.27,<2`。选择当前稳定的 v1 API，避免在 v2 正式发布前依赖预发布接口。

Server 仅使用 stdio transport。stdout 专用于 MCP 协议帧；运行期间不得向 stdout 打印授权链接、日志、调试输出或健康数据。可操作错误通过工具 JSON 返回。

## 模块边界

### `fitbit_health/mcp_server.py`

- 创建 FastMCP 实例并注册六个工具。
- 提供 `create_server(service_factory=...)`，允许测试注入 mock service。
- 提供 `main()`，调用 `mcp.run(transport="stdio")`。
- 不直接实现 OAuth、HTTP 请求、数据标准化或分析算法。

### `fitbit_health/mcp_tools.py`

- 定义 `HealthMCPService`，负责把已有管线能力组合成工具结果。
- 使用 `find_installed_credentials()` 定位桌面 OAuth 客户端。
- 使用 auth 模块提供的非交互式 saved-token loader。
- 使用 `GoogleHealthClient.fetch_all()` 获取所需数据类型。
- 使用 `normalize_results()` 和 `analyze()` 生成日级数据与统计。
- 只选择对应工具需要的字段，不复制底层 OAuth 或 API 请求实现。

### `fitbit_health/auth.py`

增加一个非交互式接口，例如：

```python
load_saved_credentials(token_path: Path, scopes: tuple[str, ...]) -> Credentials
```

行为：

- token 有效时直接返回。
- access token 过期且 refresh token 可用时自动刷新并安全写回。
- token 缺失、解析失败或刷新失败时抛出不含敏感值的 `AuthError`。
- 不启动浏览器、不输出授权 URL；提示用户在普通终端先运行 `python -m fitbit_health sync --days 1` 完成授权。

现有交互式 OAuth 流程继续供 CLI 使用。

## 工具返回 Schema

所有工具返回一个 JSON 对象，顶层字段固定为：

```json
{
  "requested_days": 7,
  "available_days": 5,
  "data": [],
  "missing_data": [],
  "diagnostics": {}
}
```

规则：

- `requested_days`：经验证的请求天数，范围 1 到 365。
- `available_days`：该工具主数据字段非空的日数。
- `data`：JSON 数组或 JSON 对象；字段按工具固定。
- `missing_data`：无有效主数据的 ISO 日期字符串数组。
- `diagnostics`：数据类型错误或安全错误信息；不得包含 token、client secret、Authorization header 或完整原始响应。

认证错误仍返回同一顶层 schema：`available_days` 为 0、`data` 为空、`missing_data` 为空，并在 `diagnostics.authentication` 中给出清晰提示。

`days` 不在 1 到 365 之间时正常返回结构化 validation 诊断，不抛出未处理异常。

## 各工具数据

- `get_sleep`：日期、睡眠分钟、清醒分钟、deep/REM/light 分钟、开始与结束时间。
- `get_steps`：日期和步数。
- `get_heart_rate`：日期和每日平均心率；不返回分钟级原始样本，避免巨大上下文。
- `get_resting_heart_rate`：日期和每日静息心率。
- `get_hrv`：日期和每日 RMSSD。
- `get_health_summary`：复用 `analyze()`，返回五类指标的 7 天/30 天统计、睡眠规律性和数据质量，不返回 Markdown。

单指标工具只请求对应 Google Health data type；summary 工具请求五类数据。某一数据类型失败时，其他类型继续返回。

## OAuth 与隐私

- MCP Server 启动本身不触发 OAuth。
- 工具首次调用时加载 `.private/token.json`。
- token 可刷新时自动刷新。
- 需要重新授权时返回安全诊断，用户在 MCP 之外运行普通 CLI 完成授权。
- stdout 只承载 MCP 消息。
- stderr 也不输出 token、原始 API 响应或健康数值。
- OAuth JSON、token、报告和个人健康数据继续受 `.gitignore` 保护。

## 错误处理

- token 缺失：返回 authentication 诊断和 bootstrap 命令。
- token 无效/刷新失败：返回重新授权提示，不包含异常原文中的敏感值。
- 单一 API 失败：对应工具正常返回空 data、缺失日期和数据类型诊断。
- summary 部分失败：保留其他指标数据，diagnostics 标记失败类型。
- 所有 API 失败：返回空数据与聚合诊断，不让 Server 崩溃。
- 无数据：返回空/空值数据、完整 missing_data，Server 保持可用。
- 非法 days：返回 validation 诊断。

## 测试设计

### 单元测试

- saved-token loader：有效 token、刷新成功、缺失、无 refresh token、刷新失败。
- 六个工具的 schema、日期范围和字段选择。
- 空数据、部分失败、认证失败和非法 days。
- 返回值可由 `json.dumps()` 序列化。
- 测试只使用 mock client 和合成数据。

### stdio 集成测试

- 生产入口启动后完成 `initialize`。
- `list_tools` 精确返回六个工具。
- 测试专用子进程使用 `create_server(service_factory=...)` 注入 fake service。
- 通过官方 `ClientSession` 和 `stdio_client` 调用至少一个 mock 工具。
- 验证 `structuredContent` 或工具文本内容能解析为规定 JSON schema。
- 不读取真实 token，不访问 Google。

## 命令与客户端配置

新增控制台入口：

```text
fitbit-health-mcp
```

等价模块入口：

```text
python -m fitbit_health.mcp_server
```

最终 README 提供 ChatGPT/Codex stdio 配置示例，包含绝对 Python 路径、`-m fitbit_health.mcp_server` 参数和工作目录 `E:\CodeX_Lab`。配置不得包含 OAuth secret 或 token。

