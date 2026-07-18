# Fitbit Health 本地数据管线

这个项目使用 Google Health API 只读同步 Fitbit Air 健康数据，在本地生成最近 7 天与 30 天趋势。它不会调用 LLM，不会上传健康数据，也不会给出医学诊断。

## 当前范围

- 睡眠时长与作息规律性
- 步数
- 平均心率
- 每日静息心率
- 每日 HRV（RMSSD）
- 标准化 JSON、分析 JSON 和中文 Markdown 报告
- 供 ChatGPT/Codex 本地调用的 stdio MCP Server

网页 Dashboard 和云端定时任务不在当前范围内。

## 安装

要求 Python 3.12 或更高版本。在项目目录运行：

```powershell
python -m pip install -e ".[test]"
```

项目目录必须包含 Google Cloud 下载的“桌面设备”OAuth JSON。程序会忽略 Web 客户端凭据，并且凭据文件已被 `.gitignore` 排除。

## 首次同步

```powershell
python -m fitbit_health sync --days 14
```

`days` 只支持 `14`、`7`、`3`、`1` 四档；最大抓取范围为 14 天，默认值为 7 天。

首次运行会启动一个临时 localhost 回调服务，并在命令输出中显示 Google 授权链接。复制完整链接到浏览器打开，使用 Fitbit Air 所属的 Google 账号，批准以下三个只读权限：活动与健身、睡眠、健康指标与测量。

localhost 只会在命令运行期间响应；直接在浏览器打开 localhost 并不会启动程序。

授权后 token 保存在 `.private/token.json`。后续运行会自动刷新 token。Google OAuth 项目处于 Testing 状态时，refresh token 可能在 7 天后失效，需要重新授权。

## 输出

输出位于 `reports/`：

- `daily_health_summary.json`：按本地日期组织的标准化数据
- `health_analysis.json`：带样本数的趋势统计
- `health_report.md`：中文趋势报告

上述目录、OAuth 凭据和 token 均不会被 Git 跟踪。

## 测试

```powershell
python -m pytest -q
python -m compileall -q src tests
```

测试只使用合成数据，不包含真实健康记录。

## 本地 MCP Server

MCP Server 复用同一套 OAuth、Google Health API、标准化与分析逻辑。它只使用本地 stdio，不启动 Web Server，也不暴露 HTTP 端口。

首次使用 MCP 前，请在普通终端完成一次交互授权：

```powershell
python -m fitbit_health sync --days 1
```

授权成功后，可用任一方式启动：

```powershell
fitbit-health-mcp
python -m fitbit_health.mcp_server
```

可用工具：

- `get_sleep(days: int = 7)`
- `get_steps(days: int = 7)`
- `get_heart_rate(days: int = 7)`
- `get_resting_heart_rate(days: int = 7)`
- `get_hrv(days: int = 7)`
- `get_health_summary(days: int = 7)`

`days` 只支持 `14`、`7`、`3`、`1` 四档；最大抓取范围为 14 天，默认值为 7 天。

每个工具都返回 JSON 对象，固定包含 `requested_days`、`available_days`、`data`、`missing_data` 和 `diagnostics`。缺失数据、单一 API 类型失败和授权失效都会作为诊断返回，不会让 MCP Server 崩溃。

### Codex TOML 配置示例

```toml
[mcp_servers.fitbit_health]
command = "D:\\anaconda\\python.exe"
args = ["-m", "fitbit_health.mcp_server"]
cwd = "E:\\CodeX_Lab"
```

### ChatGPT/Codex JSON 配置示例

```json
{
  "mcpServers": {
    "fitbit_health": {
      "command": "D:\\anaconda\\python.exe",
      "args": ["-m", "fitbit_health.mcp_server"],
      "cwd": "E:\\CodeX_Lab"
    }
  }
}
```

这里的 Python 路径来自本机 `(Get-Command python).Source`。如果环境改变，请替换成新的绝对路径。配置中不要加入 client secret、access token 或 refresh token。

如果工具返回 `diagnostics.authentication`，请退出 MCP 客户端，在普通终端重新运行 `python -m fitbit_health sync --days 1`，授权完成后再重启 MCP 客户端。MCP 进程自身不会打开浏览器或输出 OAuth 授权链接，以免污染 stdio 协议。

## 常见问题

### 浏览器提示 localhost 无法访问

必须先运行同步命令。只有程序运行时，授权链接中的随机本地回调端口才存在。不要手动打开旧的 `localhost:8080/oauth2/callback` 地址。

### Google 提示应用无权访问

确认登录邮箱已加入 Google Auth Platform 的测试用户，并在“数据访问”中启用了三个 Google Health 只读 scope。

### 某类数据为空

设备能力、佩戴情况、同步状态和授权范围都可能影响数据可用性。报告会显示有效样本数和 API 诊断，不会用推测值填补缺失数据。

### Token 刷新失败

关闭正在运行的同步程序，将 `.private/token.json` 移出项目后重新运行同步并授权。不要删除 OAuth 客户端凭据。

## 免责声明

本项目仅描述可穿戴设备数据趋势，不构成医疗诊断、治疗或用药建议。如有健康疑虑，请咨询合格的医疗专业人员。
