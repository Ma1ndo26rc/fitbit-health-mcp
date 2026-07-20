# Goal

将当前仅在本机运行的 Fitbit Health MCP 迁移为一个可由 ChatGPT 通过公网 HTTPS 调用的单用户 Remote MCP 服务：

```text
ChatGPT
    |
HTTPS Streamable HTTP MCP Endpoint
    |
Remote MCP Server
    |
existing fitbit_health Python service
    |
Google Health API
```

目标是部署现有 Python 能力，不是重写 MCP 基础设施。Remote 版本必须继续复用同一套 `create_server()`、六个 tools、Google Health client、normalize、analytics 和结构化 envelope。

本计划只做部署设计，不授权部署或代码实现。最终方案不是 OpenAI Secure MCP Tunnel，也不是临时暴露本机进程。

# Current State

当前本地架构：

```text
stdio MCP
    |
fitbit_health
    |
Google Health API
```

Phase 0 已冻结以下契约：

- 六个 tools：`get_sleep`、`get_steps`、`get_heart_rate`、`get_resting_heart_rate`、`get_hrv`、`get_health_summary`。
- `days` 只允许 `14, 7, 3, 1`，默认 `7`。
- 输出固定包含 `requested_days`、`available_days`、`data`、`missing_data` 和 `diagnostics`。
- stdio MCP 与非法参数的结构化错误行为已有测试保护。

Phase 1 已完成：

- `src/fitbit_health/http_mcp_server.py` 通过 `create_server()` 创建 FastMCP Server。
- `create_http_app()` 复用 FastMCP 的 Streamable HTTP ASGI app。
- 本地 MCP endpoint 为 `http://127.0.0.1:8000/mcp`。
- HTTP 与 stdio 没有第二套 tool registry。
- 当前测试基线为 `107 passed`。

当前 HTTP bootstrap 已具备正确的协议层，但还不是公网服务：

- FastMCP 默认监听 `127.0.0.1:8000`；远程平台要求监听 `0.0.0.0` 和平台提供的端口。
- 当前没有独立的 MCP caller authentication；任何能访问 endpoint 的调用方理论上都能调用 tools。
- 当前 Google OAuth 凭据依赖项目工作目录中的 `.private/token.json`，刷新后会写回同一文件。
- `HealthMCPService` 还会检查项目根目录中存在唯一的 `client_secret_*.json`。
- 当前文件系统模型适合本地单进程，不适合无持久磁盘或多副本运行时。

FastMCP 已支持通过 `FASTMCP_HOST`、`FASTMCP_PORT` 和 `FASTMCP_STREAMABLE_HTTP_PATH` 等环境配置调整运行边界，因此第一次远程部署不需要复制或重写 MCP tools。

# Deployment Options

## Option A: Render Python Web Service

在 Render 上创建一个单实例 Python Web Service，直接运行现有 `fitbit_health.http_mcp_server`。Render 在边缘终止 TLS，并把 HTTPS 请求转发到应用监听的 HTTP port。Render 要求 Web Service 监听 `0.0.0.0`，建议使用平台提供的 `PORT` 环境变量；这些要求可以通过 FastMCP 环境配置满足。[Render Web Services](https://render.com/docs/web-services)

建议的运行形态：

```text
ChatGPT
    |
https://<service>.onrender.com/mcp
    |
Render managed TLS / HTTPS ingress
    |
single Python Web Service instance
    |
FastMCP Streamable HTTP
    |
existing create_server() and HealthMCPService
```

评价：

| 角度 | 结论 |
| --- | --- |
| 对当前项目侵入程度 | 低。主要是部署配置、启动命令和后续的 HTTP auth boundary；不需要重写 tools 或 Google Health client。 |
| 是否复用现有代码 | 高。直接运行 `http_mcp_server.py`，继续调用同一个 `create_server()`。 |
| OAuth token | `.private/token.json` 必须位于可写的 persistent disk；Google client credential 通过 secret 注入，不进入 Git 或镜像。 |
| ChatGPT Custom App | 低到中。Render 自动提供公网 HTTPS URL；完成 MCP caller auth 后可直接填入 `/mcp` 并扫描 tools。 |
| 后续维护成本 | 低。平台管理 TLS、域名、进程重启和基础部署；仍需维护 Python 依赖、磁盘、secret 和 auth。 |

关键限制：

- Render 默认文件系统是临时的；只有 persistent disk mount path 下的文件会跨部署和重启保存。[Render Persistent Disks](https://render.com/docs/disks)
- persistent disk 只能挂载给一个 service instance；挂盘后不能横向扩展到多个实例，并且部署时会有短暂中断。这与当前单用户、单 token、单进程模型一致，但必须明确接受。
- 免费实例不适合作为目标：当前方案需要持久磁盘和稳定在线行为。
- `.private` 应直接挂载为持久目录，例如 `/opt/render/project/src/.private`，使现有相对路径继续成立。
- `client_secret_*.json` 不能提交到仓库。它应由 Render Secret File 或 secret environment 在启动时放到应用工作目录中的预期文件名；它不需要位于 persistent disk，也不能出现在构建日志。
- MCP endpoint 不能以匿名模式承载私人健康数据。必须在调用 `HealthMCPService` 之前完成 MCP caller authorization。

## Option B: Cloudflare Worker as MCP Gateway

Worker 可在边缘接收 Streamable HTTP MCP、执行 OAuth/authorization，再把请求代理到一个 Python origin。Cloudflare 官方也支持直接在 Worker 中实现 Streamable HTTP MCP，但其主路径是 Workers/Agents SDK 与 JavaScript/TypeScript MCP Server。[Cloudflare MCP transport](https://developers.cloudflare.com/agents/model-context-protocol/protocol/transport/)

作为 Gateway 时的真实架构是：

```text
ChatGPT
    |
Cloudflare Worker: HTTPS + MCP caller auth
    |
Python origin on Render, Fly.io, or a VM
    |
existing fitbit_health
```

因此 Worker Gateway 不是 Python runtime 的替代方案；它是在 Option A 或 Option C 前面再增加一层。若不保留 Python origin，而直接在 Worker 内实现 tools 和 Google Health 调用，就需要重写现有 Python MCP、auth、client、normalize 和 analytics，违反复用目标。

评价：

| 角度 | 结论 |
| --- | --- |
| 对当前项目侵入程度 | 中到高。Gateway 模式需要新增 Worker 项目、代理协议、origin 认证和双端配置；重写模式则侵入最高。 |
| 是否复用现有代码 | Gateway 模式可复用 Python，但仍需另一个 Python host；Worker 原生实现无法直接复用当前 CPython 服务。 |
| OAuth token | Google token 仍应保存在 Python origin，不应复制到 Worker。Worker 只负责 MCP caller token；两类 token 必须隔离。 |
| ChatGPT Custom App | 中。Worker 可提供稳定 HTTPS 和 OAuth，但必须正确转发 Streamable HTTP、SSE、MCP session headers、401 challenge 和 request body。 |
| 后续维护成本 | 高于 Option A。需要同时维护 Worker、Python origin、两层 secret、两层日志和代理兼容性。 |

Cloudflare Python Workers 运行在 Pyodide/WebAssembly 中，只支持 pure/PyEmscripten packages，文件系统是 isolate 内的临时内存；持久状态需要 KV、R2 或 Durable Objects。[Cloudflare Python packages](https://developers.cloudflare.com/workers/languages/python/packages/) [Cloudflare Python filesystem](https://developers.cloudflare.com/workers/languages/python/stdlib/)

这与当前 CPython 3.12、`mcp`、Google auth libraries 和可写 token file 的运行假设不匹配。即使能逐项适配，也会形成新的运行时和 token storage 设计，因此本项目当前不推荐 Worker Gateway 或 Python Worker 作为第一版目标。

## Option C: Fly.io or a small managed VM/container

把当前 Python 项目打包成普通 Linux container，在 Fly.io Machine 或小型 VM 上运行；由平台或反向代理提供 TLS，并把一个持久 volume 挂载到 token 目录。

评价：

| 角度 | 结论 |
| --- | --- |
| 对当前项目侵入程度 | 低到中。业务代码复用度高，但需要 Dockerfile、平台配置、volume、进程与网络运维。 |
| 是否复用现有代码 | 高。运行同一个 Python module 和 `create_server()`。 |
| OAuth token | 挂载单机 volume；Google secrets 通过平台 secret store 注入。Fly Volume 默认支持静态加密，但仍需单实例和备份策略。[Fly Volumes](https://fly.io/docs/volumes/overview/) |
| ChatGPT Custom App | 中。获得公网 HTTPS `/mcp` 后接入流程与 Render 相同；caller auth 仍必须单独实现。 |
| 后续维护成本 | 中。比 Render 拥有更多网络、容器、volume、升级和故障恢复控制，同时也承担更多操作责任。 |

Option C 适合以后需要更强的容器控制、固定区域或自定义反向代理时使用。对当前单用户 MVP，增加的运维自由度没有抵消 Render 的简单性。

## Recommendation

推荐 **Option A：Render 单实例 Python Web Service + persistent disk**。

原因：

1. 它是把当前本地 Streamable HTTP adapter 迁移到公网 HTTPS 的最短路径。
2. 最大程度复用现有 Python 代码和测试过的 MCP contract。
3. persistent disk 能支持当前 refresh token 的原地刷新，不需要数据库。
4. Render 管理 TLS、公开域名和基础进程生命周期，ChatGPT 可直接连接标准 `/mcp` URL。
5. 单实例限制与当前单用户架构一致，避免过早引入并发 token storage、数据库或多租户设计。

Cloudflare Worker 不作为第一版 Gateway。只有未来确实需要边缘 OAuth、WAF、统一入口或隐藏多个 origin 时，才单独评估在 Render 前增加 Gateway；它不能替代 Python origin。

# Recommended Architecture

## Current

```text
stdio MCP
    |
fitbit_health
    |
Google Health API
```

## Future

```text
ChatGPT Custom App
    |
HTTPS Streamable HTTP MCP
    |
Render managed TLS ingress
    |
MCP caller authentication boundary
    |
single Render Python runtime
    |
fitbit_health.http_mcp_server
    |
existing create_server()
    |
existing HealthMCPService
    |
existing GoogleHealthClient / normalize / analytics
    |
Google Health API

Persistent runtime state:
    /opt/render/project/src/.private/token.json
        on a single attached persistent disk

Injected runtime secret:
    client_secret_*.json
        supplied outside Git and outside the image
```

责任边界：

- **Render ingress**：公网 DNS、TLS termination、HTTPS redirect、向应用端口转发。
- **MCP caller auth boundary**：验证 ChatGPT 代表的已授权用户，只允许健康数据只读 scope；失败时在读取 Google credentials 前返回 `401`。
- **FastMCP transport**：处理 `/mcp` 的 Streamable HTTP 协议和 session lifecycle。
- **现有 Python 服务**：继续拥有 tool schema、数据读取、normalize、analytics、diagnostics 和 redaction。
- **Google OAuth credential**：仅存在于远程 Python runtime 的受限 secret/disk 边界内，不发送给 ChatGPT。

ChatGPT 当前的 developer-mode app 流程要求 MCP Server 可通过 HTTPS 访问，并允许填写公开 `/mcp` endpoint 后发现 tools。[Connect from ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt)

现有六个 tools 可以直接作为普通 app tools 被扫描；它们不需要改造成 `search`/`fetch`。`search`/`fetch` 只与 Company Knowledge 等特定检索场景有关。后续可以在不改变输入输出契约的前提下评估给六个 tools 增加 `readOnlyHint: true`，但这不是部署前置条件。

# OAuth Strategy

远程架构中存在两个完全独立的认证关系，不能混用。

## 1. ChatGPT to Remote MCP

这是 MCP caller authentication，用于确认谁可以调用私人健康 tools。

私人健康数据不应使用匿名 `noauth`。OpenAI 当前文档要求 authenticated MCP Server 实现符合 MCP authorization spec 的 OAuth 2.1 resource-server flow；ChatGPT 支持 authorization code + PKCE，以及 CIMD、DCR 或预定义 OAuth client。[OpenAI Apps authentication](https://developers.openai.com/apps-sdk/build/auth)

第一版应使用成熟的外部 identity provider，而不是自行编写 authorization server。Remote MCP 负责：

- 发布 protected-resource metadata。
- 对每个 MCP request 验证 bearer token 的 signature、issuer、audience/resource、expiry 和 read scope。
- 未授权时返回带正确 `WWW-Authenticate` 的 `401`。
- 在认证通过前不创建 `HealthMCPService`、不读取 `.private/token.json`、不调用 Google Health。

这层 OAuth 不得使用 Google Health refresh token，也不得把 Google access token交给 ChatGPT。

## 2. Remote MCP to Google Health

这是现有 Google Installed App/Desktop OAuth grant，用于读取单个用户的 Google Health 数据。本阶段不修改 Google OAuth flow。

迁移策略：

1. 继续在本地完成 Google OAuth 授权，不在公网 MCP request 中启动浏览器或 localhost callback。
2. 将已授权的 `.private/token.json` 通过受控运维通道一次性放入远程 persistent disk。
3. 将现有 `client_secret_*.json` 通过 Render secret 机制注入运行时预期路径，绝不提交 Git、构建镜像或普通环境日志。
4. Remote MCP 调用 `load_saved_credentials()`；access token 有效时直接使用。
5. access token 过期且 refresh token 有效时，现有逻辑调用 Google token endpoint 刷新，并把更新后的 token JSON 写回 persistent disk。
6. 若 refresh token 被撤销、过期或 Google Testing policy 使其失效，远程服务返回现有安全 authentication diagnostic；重新在本地授权，再通过受控运维流程替换远程 token。

## Refresh token persistence

- token 文件必须位于持久磁盘，而不是 Render 的 ephemeral root filesystem、Secret File 或 image layer。
- 只运行一个 service instance，避免两个进程同时刷新并覆盖同一文件。
- `.private` 目录和 `token.json` 应设置为仅运行用户可读写；平台 secret 和部署日志不得打印文件内容。
- persistent disk snapshot 包含 refresh token，应按 credential backup 对待；限制平台账户访问并定义撤销/替换流程。
- 第一版不新增数据库。若未来需要多实例或多用户，必须先重新设计 token store、加密、锁和租户隔离，不能直接扩容当前文件模型。

## Google credential leakage controls

以下内容不得进入 Git、Docker image、build cache、HTTP response、MCP structured content、application logs 或 error trace：

- `client_secret_*.json`
- `.private/token.json`
- Google access token
- Google refresh token
- MCP bearer token
- HTTP `Authorization` header
- 原始 Google Health payload 或完整健康数据日志

# Migration Steps

以下是进入实施阶段后的推荐顺序；本计划不执行任何一步。

## Phase 2A: Deployment contract freeze

1. 保留当前 107-test baseline。
2. 记录六个 tools、schemas、envelope 和 `/mcp` path 的远程验收快照。
3. 确认 stdio MCP、CLI 和本地 OAuth 行为不变。
4. 明确只支持单用户、单实例、只读 Google Health。

Exit criterion：远程化不能改变现有 MCP contract。

## Phase 2B: Render runtime packaging

1. 选择 Render Python runtime 或最小 Docker image；优先原生 Python runtime，除非依赖验证要求 Docker。
2. 固定 Python 3.12 compatible patch version，不使用平台未来默认版本。
3. 安装当前 package 和 dependencies。
4. 通过环境配置把 FastMCP 绑定到 `0.0.0.0:$PORT`，保持 `/mcp`。
5. 只增加不读取 Google 数据的 readiness/health strategy；在没有专用 health route 前使用 TCP health check。

Exit criterion：无真实 secrets 的远程 runtime 能启动，并通过 MCP initialize/tools-list synthetic test。

## Phase 2C: MCP caller authorization

1. 选择支持 MCP OAuth 2.1 要求的成熟 identity provider。
2. 配置 protected-resource metadata、authorization-server metadata、PKCE 和 ChatGPT client registration mode。
3. 在 Python HTTP 边界验证 bearer token 和 `health:read` scope。
4. 验证未授权请求不会加载 Google token或调用 Google Health。

Exit criterion：公网 `/mcp` 对匿名请求关闭，并能被 ChatGPT 完成授权。

## Phase 2D: Single-user Google credential migration

1. 创建 persistent disk 并挂载到应用 `.private` 目录。
2. 通过安全通道放入已授权 `token.json`。
3. 通过 Render secret 机制注入 Google installed-app client credential。
4. 验证 access-token refresh 会写回 persistent disk，并在 restart 后仍有效。
5. 验证日志、HTTP 错误和 MCP diagnostics 不包含任何 credential。

Exit criterion：远程进程能在不修改 Google OAuth flow 的情况下持续刷新单用户 token。

## Phase 2E: Private staging validation

1. 先用 fake client 完成公网 HTTPS MCP initialize、tools/list 和 tools/call。
2. 再用真实账号最小调用 `get_steps(days=1)`。
3. 验证 `get_health_summary(days=7)`、missing data、Google API failure 和 refresh failure。
4. 验证 restart、redeploy 短暂中断、disk snapshot 和 token replacement runbook。
5. 检查 request/response logging 已关闭或完成 payload redaction。

Exit criterion：HTTPS、auth、persistent token、contract 和 privacy checks 全部通过。

## Phase 2F: ChatGPT Custom App connection

1. 在 ChatGPT 开启 developer mode。
2. 创建 private/draft app，填写 `https://<service>.onrender.com/mcp`。
3. 完成 MCP caller OAuth，而不是 Google Health OAuth。
4. 扫描并核对六个 tools 的 frozen snapshot。
5. 在新 ChatGPT conversation 中调用 steps、sleep、HRV 和 summary。
6. 在明确评估健康数据处理政策前，不发布到公共 plugin directory。

Exit criterion：ChatGPT 能通过 HTTPS Remote MCP 调用现有 Fitbit Health Agent，且 Google credentials 始终只在服务端。

# Risks

## Public endpoint without caller authentication

当前 HTTP Server 没有 caller auth。若直接部署，任何互联网用户都可能读取健康数据。认证必须先于真实 token migration 和真实数据调用完成。

## Token loss on ephemeral filesystem

如果 token 放在默认 filesystem 或只读 Secret File，部署、restart 或刷新写回会导致凭据丢失或失败。必须使用 persistent disk。

## Refresh write race

当前 token 文件模型没有多进程刷新协调。第一版必须保持单 instance、单 worker。不能通过增加 Render instances 或多个 Python workers 扩容。

## Render disk availability trade-off

挂载 persistent disk 后无法横向扩展，并失去 zero-downtime deploy。对单用户 MVP 可接受，但部署和平台故障可能造成短暂停机。[Render disk limitations](https://render.com/docs/disks)

## Credential exposure through operations

手工 seed token、secret injection、SSH、snapshot、support bundle 和 debug logging 都可能泄露 refresh token。需要最小权限账户、受控 runbook 和禁止打印 secrets 的检查。

## Google OAuth revocation or expiry

Google refresh token 仍可能因撤销、Testing 状态或账号安全事件失效。由于本阶段不改 OAuth，恢复方式仍是本地重新授权后替换远程 token，而不是远程交互式授权。

## Confusing MCP OAuth with Google OAuth

MCP caller OAuth 授权 ChatGPT 调用 Remote MCP；Google OAuth 授权 Remote MCP 调用 Google Health。任何把两者合并、透传或复用 token 的设计都会扩大 credential 泄漏面。

## Health-data privacy

tool 结果会从 Google Health 经 Remote MCP 发送到 ChatGPT。部署前必须确认账户、workspace、hosting region、日志、retention 和适用的健康数据义务。Passing tests does not establish privacy or compliance readiness.

## ChatGPT product drift

Developer mode、app/plugin 管理、OAuth metadata 和权限 UI 仍可能变化。实施 ChatGPT connection 前必须再次核对 OpenAI 官方文档，不能把当前 UI 文案固化到协议代码中。

## Cloudflare Gateway protocol risk

如果未来添加 Gateway，必须完整保留 Streamable HTTP response streaming、SSE、MCP session headers、OAuth challenge 和 request cancellation。普通 JSON reverse proxy 测试不足以证明 MCP 代理正确。

# What Not To Change

- 不使用 OpenAI Secure MCP Tunnel 作为最终架构。
- 不把本机 stdio MCP 暴露为生产服务。
- 不修改 `src/fitbit_health/mcp_server.py` 的 stdio 启动行为。
- 不复制或重写六个 tool definitions；HTTP 与 stdio 继续共用 `create_server()`。
- 不修改 tool names、`days` enum/default 或结构化 envelope。
- 不重写 `GoogleHealthClient`、pagination、retry、normalize 或 analytics。
- 不修改当前 Google OAuth scopes、Desktop OAuth authorization flow 或 localhost callback。
- 不在 MCP request 中启动 Google interactive OAuth。
- 不把 Google refresh token 发送给 ChatGPT，也不把 Google token 当作 MCP caller token。
- 不把 `.private/token.json` 放入 Git、image、ephemeral filesystem、只读 Secret File、HTTP response 或日志。
- 不新增数据库、KV、queue、cache、多用户或多租户。
- 不重构 `HealthSyncService`，也不以部署为由清理无关的 CLI/MCP orchestration。
- 不新增 Cloudflare Worker 作为第一版 Gateway。
- 不新增第二套 Python backend 或第二套 MCP registry。
- 不在完成 caller auth、persistent token 和隐私验证前连接真实健康数据。
- 本文档不授权创建 Render 服务、域名、磁盘、identity provider、ChatGPT app 或任何外部资源。
