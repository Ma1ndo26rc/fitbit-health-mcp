# Fitbit Health 本地数据管线

这个项目使用 Google Health API 只读同步 Fitbit Air 健康数据，在本地生成最近 7 天与 30 天趋势。它不会调用 LLM，不会上传健康数据，也不会给出医学诊断。

## 当前范围

- 睡眠时长与作息规律性
- 步数
- 平均心率
- 每日静息心率
- 每日 HRV（RMSSD）
- 标准化 JSON、分析 JSON 和中文 Markdown 报告

MCP、网页 Dashboard 和云端定时任务留到下一阶段。

## 安装

要求 Python 3.12 或更高版本。在项目目录运行：

```powershell
python -m pip install -e ".[test]"
```

项目目录必须包含 Google Cloud 下载的“桌面设备”OAuth JSON。程序会忽略 Web 客户端凭据，并且凭据文件已被 `.gitignore` 排除。

## 首次同步

```powershell
python -m fitbit_health sync --days 30
```

首次运行会启动一个临时 localhost 回调服务，并自动打开 Google 授权页。请使用 Fitbit Air 所属的 Google 账号，批准以下三个只读权限：活动与健身、睡眠、健康指标与测量。

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

## 常见问题

### 浏览器提示 localhost 无法访问

必须先运行同步命令。只有程序运行时，本地回调端口才存在。

### Google 提示应用无权访问

确认登录邮箱已加入 Google Auth Platform 的测试用户，并在“数据访问”中启用了三个 Google Health 只读 scope。

### 某类数据为空

设备能力、佩戴情况、同步状态和授权范围都可能影响数据可用性。报告会显示有效样本数和 API 诊断，不会用推测值填补缺失数据。

### Token 刷新失败

关闭正在运行的同步程序，将 `.private/token.json` 移出项目后重新运行同步并授权。不要删除 OAuth 客户端凭据。

## 免责声明

本项目仅描述可穿戴设备数据趋势，不构成医疗诊断、治疗或用药建议。如有健康疑虑，请咨询合格的医疗专业人员。
