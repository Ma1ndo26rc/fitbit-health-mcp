# Remote MCP Design Draft

> Status: architecture draft only. This document does not authorize implementation, deployment, refactoring, or changes to the existing stdio MCP server.
>
> Analysis baseline: local repository state on 2026-07-20 and the `main` branch of [`Ring8688/google-health-worker-mcp-V1`](https://github.com/Ring8688/google-health-worker-mcp-V1).

## Decision Summary

The recommended direction is to keep the current Python stdio MCP server unchanged and, in a later approved phase, add a sibling Python entry point that exposes the same FastMCP server over Streamable HTTP. The remote entry point should call the existing `create_server(service_factory=...)`; it should not duplicate tool definitions, analytics, normalization, or Google Health API calls.

Three approaches were considered:

1. **Copy the reference Cloudflare Worker.** Rejected because it would introduce Cloudflare and TypeScript, duplicate the Google Health API implementation, and split behavior across two stacks.
2. **Convert the existing stdio entry point into HTTP.** Rejected because it would change a validated local integration and violate the requirement to preserve stdio MCP.
3. **Add a transport sibling around the existing FastMCP server.** Recommended because transport, caller authentication, and remote credential placement can be added at the boundary while the current Python domain path remains authoritative.

# Current Architecture

## Runtime and MCP entry point

The local product is a single-user Python application with two entry paths:

```text
CLI:          python -m fitbit_health sync --days N
stdio MCP:    python -m fitbit_health.mcp_server
              or fitbit-health-mcp
```

`src/fitbit_health/mcp_server.py` is the MCP entry point. `main()` creates the server and runs `create_server().run(transport="stdio")`. `pyproject.toml` maps the `fitbit-health-mcp` console script to this function.

`create_server()`:

- creates one `FastMCP("Fitbit Health", json_response=True)` instance;
- lazily creates one `HealthMCPService` for the process;
- registers six tools with `@server.tool(structured_output=True)`;
- exposes `days` as an integer schema with enum `[14, 7, 3, 1]` and default `7`;
- returns the server without authenticating or fetching data.

The registered tools are:

- `get_sleep`
- `get_steps`
- `get_heart_rate`
- `get_resting_heart_rate`
- `get_hrv`
- `get_health_summary`

All return the existing structured envelope: `requested_days`, `available_days`, `data`, `missing_data`, and `diagnostics`.

## Google Health API call chain

The stdio MCP request path is:

```text
MCP client
  -> FastMCP tool in mcp_server.py
  -> HealthMCPService in mcp_tools.py
  -> find_installed_credentials() / load_saved_credentials()
  -> GoogleHealthClient
  -> GET health.googleapis.com/v4/users/me/dataTypes/{type}/dataPoints:reconcile
  -> FetchResult
  -> normalize_results()
  -> metric envelope, or analyze() for get_health_summary
  -> structured MCP result
```

`HealthMCPService._metric()` fetches one data type and returns normalized daily values. `get_health_summary()` fetches all five entries in `pipeline.DATA_TYPES`, normalizes them together, and calls `analytics.analyze()`.

`GoogleHealthClient.fetch_all()` owns the existing Google Health v4 behavior: supported data-type filters, pagination, timeouts, limited retries for transient responses, safe HTTP error mapping, and `FetchResult`. This is the implementation that must remain authoritative; a remote MCP must not reimplement these calls.

## OAuth token storage

The current OAuth model is Google Installed App/Desktop OAuth:

- the desktop client configuration is discovered as the sole valid `client_secret_*.json` under the project root;
- interactive CLI authorization is handled by `load_credentials()` and `InstalledAppFlow.run_local_server()`;
- serialized Google credentials are stored in `.private/token.json`;
- stdio MCP uses `load_saved_credentials()` only, so it never starts an interactive authorization flow;
- expired credentials with a refresh token are refreshed and written back to the same file.

The file is a mutable single-user credential store, not merely a cache. It can contain access and refresh material. Its present Windows attribute handling is best-effort and is not equivalent to an owner-only ACL. The file must never be served, logged, bundled into an image, or exposed through the remote endpoint.

## Analytics and MCP coupling

The coupling is localized in `src/fitbit_health/mcp_tools.py`:

- `mcp_server.py` does not import analytics and only delegates tool calls;
- individual metric tools normalize data but do not call `analytics.analyze()`;
- only `HealthMCPService.get_health_summary()` imports and calls `analyze(normalized)`;
- `analytics.py` is transport-independent and has no MCP, OAuth, filesystem, or HTTP dependency;
- CLI and MCP currently duplicate part of the fetch/normalize/analyze orchestration in `pipeline.run_sync()` and `HealthMCPService.get_health_summary()`.

That duplication is a known architectural issue, but it is not necessary to solve it in order to design the remote transport and must not be refactored as part of this document-only task.

# Target Architecture

## Recommended shape

```text
Local Codex / desktop client
  -> stdio
  -> existing mcp_server.py (unchanged)
                         \
                          -> existing FastMCP tool definitions
                          -> existing HealthMCPService
                          -> existing auth.py / client.py
                          -> Google Health API v4
                          -> normalize.py / analytics.py
                         /
ChatGPT
  -> HTTPS POST /mcp (Streamable HTTP)
  -> remote caller-auth boundary
  -> future Python remote MCP bootstrap

Remote host persistent state:
  -> encrypted, access-restricted single-user token file
     or another mutable TokenStore implementation approved later
```

Streamable HTTP does not need to replace stdio in the codebase. It replaces stdio only on the ChatGPT-to-server network path. The installed `mcp` version already exposes FastMCP's `streamable-http` transport and `/mcp` application surface, so the future adapter can remain in Python without FastAPI or Cloudflare.

The future remote bootstrap should:

1. construct a remotely appropriate credential/client factory;
2. construct `HealthMCPService` with that factory;
3. call the existing `create_server(service_factory=...)` so the same six tools and schemas are registered;
4. attach caller authentication and transport security at the HTTP boundary;
5. expose only the MCP path through HTTPS.

It must not copy the decorators or reproduce the Google request pipeline.

## Mapping from the reference project

| Reference responsibility | Reference implementation | Python target correspondence |
| --- | --- | --- |
| Remote transport | Hono route plus `@hono/mcp` `StreamableHTTPTransport` | FastMCP native Streamable HTTP in a new sibling bootstrap |
| MCP construction | `buildServer(env)` | existing `create_server(service_factory=...)` |
| Tool registration | `registerAllTools(server, provider, env)` | existing decorators in `mcp_server.py`; no duplicated registry |
| Provider abstraction | `HealthProvider` plus `GoogleHealthProvider` | a narrow Python protocol/adapter around the existing `GoogleHealthClient` and normalization boundary, only if needed for credential injection/testing |
| Google API | TypeScript `GoogleClient` and mapper modules | existing `client.py` plus `normalize.py`; authoritative and reused |
| Token persistence | Cloudflare `TOKENS` KV | current `.private/token.json` for local; future remote mutable `TokenStore` or encrypted persistent file |
| Request guard | shared path secret plus source CIDR | standards-based caller authentication/token verification; no secret in the URL |
| Hosting/runtime | Cloudflare Worker | unspecified Python HTTPS runtime selected in a later deployment design |
| Read cache | Cloudflare `CACHE` KV | no equivalent required for the first remote phase |

## HealthProvider interpretation

The reference `HealthProvider` is a broad domain API: tools depend on methods such as sleep, activity, heart rate, and HRV, while `GoogleHealthProvider` supplies the Google-specific implementation. That design allowed the reference project to retain provider-independent tools while changing the upstream provider.

The current Python project has the same separation distributed across smaller units:

```text
GoogleHealthClient        = Google transport and raw FetchResult
normalize_results         = provider payload -> canonical daily document
analytics.analyze         = canonical document -> analysis
HealthMCPService          = application orchestration + MCP-safe envelope
```

Therefore, `HealthProvider` should not be copied as a large interface. If a protocol becomes necessary, it should be the smallest interface already consumed by `HealthMCPService`, initially equivalent to `fetch_all(data_type, start_date) -> FetchResult`. `GoogleHealthClient` already satisfies that behavioral role. A broader `HealthSyncService` may later centralize CLI/MCP orchestration, but that is a separate roadmap item and not a prerequisite for Streamable HTTP.

## Role of Cloudflare Worker in the reference

The Worker is not the health business layer. It provides:

- an internet-reachable HTTPS runtime;
- routing for `/health` and `POST /mcp/:secret`;
- creation of an MCP server and Streamable HTTP transport for a request;
- access to environment secrets and KV namespaces;
- a request guard using a path secret and allowed source CIDRs;
- deployment and edge execution.

Those are hosting and boundary concerns. None requires rewriting the existing Python Google Health client. This project should copy the separation of responsibilities, not the Cloudflare implementation.

## KV token storage versus `.private/token.json`

Both stores hold the same category of mutable OAuth state: access token, refresh token, and expiry information. Their operational properties differ:

| Property | `.private/token.json` | Reference `TOKENS` KV |
| --- | --- | --- |
| Scope | one local OS user/process | remote Worker instances |
| Mutation | atomicity depends on file write behavior | networked key updates |
| Concurrency | effectively single-process today | multiple requests may refresh concurrently |
| Security boundary | filesystem permissions | cloud account, bindings, and provider controls |
| Suitability | local CLI/stdio | remote stateless runtime |

KV is not intrinsically the design goal. Its architectural lesson is that a remote runtime needs a mutable credential store independent of a request instance. Under the present constraints, the first target remains single-user and may use an encrypted persistent volume with a tightly restricted token file. Before multi-replica or multi-user support, a `TokenStore` abstraction, refresh locking, atomic updates, encryption, rotation, and revocation would become mandatory. No database or KV service is proposed in this draft.

## ChatGPT remote endpoint

The eventual connection surface is an HTTPS Streamable HTTP MCP endpoint such as:

```text
https://health-mcp.example.com/mcp
```

In ChatGPT's current custom-app flow, an authorized user or workspace admin enables developer mode, creates an app under **Settings / Workspace Settings -> Apps -> Create**, supplies the MCP endpoint and authentication choice, and runs **Scan Tools**. If OAuth is configured, ChatGPT follows the authorization flow before scanning tools. ChatGPT cannot connect directly to the existing local stdio process; the endpoint must be remotely reachable, or a separately approved secure tunnel product must be used.

The reference repository's `/mcp/<shared-secret>` URL is a Claude-specific deployment choice and should not be copied as the target security model. Secrets in URLs can leak through browser history, access logs, proxies, monitoring, and screenshots. The target should use HTTPS plus standards-based bearer/OAuth validation at the MCP boundary, independent of the Google OAuth grant used downstream. Because ChatGPT custom-app capabilities and plan controls are evolving, the exact supported authentication option and OAuth metadata must be revalidated against current OpenAI documentation during the implementation phase.

There are two distinct trust relationships:

1. **ChatGPT -> remote MCP:** authenticates the MCP caller and authorizes access to read-only health tools.
2. **Remote MCP -> Google:** uses the user's Google OAuth grant and refresh token to read Google Health data.

These tokens must never be interchangeable. ChatGPT should never receive the Google refresh token, and Google credentials should never be used as MCP caller credentials.

# Components to Reuse

## `auth.py`

Reuse its Google credential parsing, refresh semantics, scope handling, and safe user-facing authentication failures. Keep `load_credentials()` as the local interactive CLI path and `load_saved_credentials()` as the local stdio path.

Remote use requires a different storage boundary and must not trigger `InstalledAppFlow.run_local_server()` inside an MCP request. The smallest future change is to adapt credential loading around a remote-safe token location/provider while retaining the existing Google credential objects and refresh logic. This document does not make that change.

## `client.py`

Reuse `GoogleHealthClient` unchanged as the single implementation of Google Health v4 requests, filters, pagination, timeouts, retries, and `FetchResult`. A remote adapter supplies credentials; it does not replace the client.

## `analytics.py`

Reuse `analyze()` and its pure metric functions unchanged. Analytics remains behind the application service and must stay unaware of stdio versus HTTP.

## `mcp_tools.py`

Reuse `HealthMCPService`, its validation, normalized outputs, diagnostic redaction, and structured envelopes. Dependency injection through `client_factory` already provides the seam needed by a future remote bootstrap.

## Supporting modules

Also reuse `normalize.py`, `fetch_window.py`, `config.SCOPES`, and the tool names/schema/envelope defined by `mcp_server.py`. Preserve the allowed day values `14, 7, 3, 1` and default `7` across both transports.

# Components To Add

These are future components, not files authorized by this task:

1. **Remote MCP bootstrap.** A small Python entry point that imports `create_server`, injects a remote-safe `HealthMCPService`, and starts FastMCP's native Streamable HTTP transport on `/mcp`.
2. **MCP caller-auth boundary.** HTTPS enforcement and token verification appropriate for ChatGPT custom apps. Prefer OAuth-compatible bearer validation; do not place a shared secret in the path.
3. **Remote token-store adapter.** A minimal interface for reading and atomically replacing the Google credential bundle. The first approved single-user version may use an encrypted mounted file; no database is required.
4. **Refresh coordination.** A per-user/single-grant lock and one-time 401 refresh-and-replay behavior before any multi-request remote exposure.
5. **Remote configuration.** Explicit host, port, public MCP path, trusted proxy/origin handling, public base URL, Google OAuth client settings, and log-redaction policy. Configuration must not contain real tokens in source control.
6. **Boundary-focused tests.** Contract parity between stdio and HTTP tool lists/schemas/results, unauthorized request rejection, token refresh behavior, concurrency, redaction, timeouts, and health-data leakage checks.
7. **Operational controls.** Minimal health/readiness checks that do not touch Google data, request IDs, rate limits, bounded response sizes, and logs that exclude tool payloads and credentials.

Not included: Cloudflare Workers, Hono, FastAPI, a database, a cache, a browser UI, background synchronization, or multi-user tenancy.

# Migration Steps

Each step requires separate approval. This document does not execute any step.

## Phase 0: Freeze current contracts

- Record the six tool names, input schemas, default/allowed windows, structured envelopes, and stdio handshake as compatibility tests.
- Confirm that the work does not change CLI output or local OAuth behavior.
- Resolve the existing token-file ACL issue before placing any credential file on a remote host.

Exit criterion: current CLI and stdio behavior is explicitly protected.

## Phase 1: Prove transport parity locally

- Add only a sibling remote bootstrap using FastMCP native Streamable HTTP.
- Build the server through the existing `create_server(service_factory=...)`.
- Use fake credentials/client fixtures; do not expose it publicly and do not deploy.
- Verify `initialize`, `tools/list`, and all six `tools/call` responses match stdio contracts.

Exit criterion: HTTP is only a transport adapter, with no duplicated tools or Google API code.

## Phase 2: Separate caller auth from Google auth

- Add MCP caller token verification and explicit read-only authorization.
- Keep Google OAuth credentials exclusively behind `auth.py`/the token-store adapter.
- Reject unauthenticated requests before constructing a health client or loading Google credentials.
- Revalidate ChatGPT's current custom-app authentication and metadata requirements.

Exit criterion: no request can reach health tools without MCP-layer authorization, and no Google token appears outside the server.

## Phase 3: Make token persistence remote-safe

- Select an approved single-user encrypted persistent storage mechanism.
- Implement atomic writes, restrictive permissions, refresh coordination, revocation, and backup/restore rules.
- Keep `.private/token.json` unchanged for local CLI/stdio; remote configuration points to a separate store/location.
- Do not add multi-user storage or a database.

Exit criterion: access-token refresh survives process restarts without credential leakage or concurrent overwrite.

## Phase 4: Private end-to-end validation

- Run the remote endpoint only in a controlled non-public environment.
- Test with synthetic or minimum-scope data first.
- Validate tool discovery, request windows, diagnostics, timeouts, redaction, and read-only annotations.
- Conduct a privacy/security review specifically for health data sent through ChatGPT and the hosting provider.

Exit criterion: the endpoint passes protocol, authentication, privacy, and failure-mode checks.

## Phase 5: ChatGPT custom-app registration

- Place the validated endpoint behind production HTTPS.
- In ChatGPT developer mode, create the custom app with the `/mcp` endpoint and approved authentication mechanism.
- Complete authorization if required, scan tools, and verify the exact six-tool snapshot.
- Keep the app private/draft until an explicit publish decision and policy review.

Exit criterion: ChatGPT can call the read-only tools without changing the stdio MCP or exposing credentials.

# Risks

## Credential and privacy risk

Health data and OAuth refresh material are highly sensitive. Logging request bodies, MCP results, authorization headers, token refresh responses, or raw Google payloads is unacceptable. Remote MCP also sends selected tool outputs to ChatGPT, so data retention, residency, workspace policy, consent, and applicable health-data obligations must be reviewed before real-user use.

## Authentication-model confusion

The reference project's path secret/CIDR guard authenticates Claude-to-Worker traffic; it does not authorize Google Health access and is not a portable ChatGPT design. Conflating MCP caller auth with Google OAuth would expose grants or allow unauthorized health access.

## Token refresh concurrency

The current file flow assumes a local, mostly serial process. Concurrent HTTP calls can refresh and overwrite the same token simultaneously. Remote exposure requires locking, atomic replacement, and one controlled 401 replay.

## Stateless transport versus mutable credentials

Streamable HTTP can be stateless per request, while Google tokens are mutable and persistent. Process memory alone is insufficient. A host without durable encrypted storage will lose refresh state across restarts.

## Public endpoint attack surface

An internet-reachable endpoint adds scanning, denial-of-service, oversized payload, timeout, proxy-header, TLS, and dependency risks. Authentication must run before health-client construction, and rate/size/time limits must be explicit.

## Contract drift

Duplicating decorators or tool handlers for HTTP would let stdio and remote schemas diverge. Both transports must construct the same FastMCP server and use contract-parity tests.

## Existing semantic issues

Known issues such as sleep availability, timezone boundaries, and any remaining orchestration duplication do not disappear under HTTP. They should be handled as separately approved local correctness tasks, not hidden inside the remote migration.

## ChatGPT product evolution

Custom-app availability, plan controls, authentication choices, approval behavior, and publishing workflow are product-dependent and may change. Revalidate the endpoint and OAuth requirements immediately before implementation and registration.

## Reference-project assumptions

The reference repository is useful as an architecture example, not as proof of production readiness. Its broad tool surface, URL secret, provider-specific mappings, Cloudflare bindings, and Claude CIDR assumptions should not be inherited without independent review.

# What NOT To Change

- Do not change `src/fitbit_health/mcp_server.py` stdio startup behavior.
- Do not remove or replace the current `fitbit-health-mcp` console script.
- Do not change the six tool names, the `days` enum/default, or the existing structured envelope.
- Do not reimplement Google Health API calls, pagination, filters, retries, or mappings outside `client.py` and `normalize.py`.
- Do not move analytics into the HTTP/MCP transport layer.
- Do not make CLI OAuth interactive behavior available inside remote requests.
- Do not expose `.private/token.json`, client secrets, access tokens, refresh tokens, authorization headers, raw health payloads, or reports over HTTP or in logs.
- Do not reuse the Google OAuth token as ChatGPT/MCP caller authentication.
- Do not adopt Cloudflare Worker, Hono, TypeScript, FastAPI, a database, KV, cache, queue, background worker, or multi-user tenancy as part of this migration design.
- Do not restore a 30-day fetch option; preserve `14, 7, 3, 1` with default `7`.
- Do not perform unrelated `HealthSyncService`, DTO, report, ACL, timezone, or sleep-normalization refactors under the remote MCP task. If required, each is a separate approved change.
- Do not deploy, publish a ChatGPT app, open a public port, or modify Codex/ChatGPT configuration until a later task explicitly authorizes it.

## Sources Consulted

- Reference repository: [`README.md`](https://github.com/Ring8688/google-health-worker-mcp-V1/blob/main/README.md), [`src/index.ts`](https://github.com/Ring8688/google-health-worker-mcp-V1/blob/main/src/index.ts), [`src/server.ts`](https://github.com/Ring8688/google-health-worker-mcp-V1/blob/main/src/server.ts), [`src/providers/types.ts`](https://github.com/Ring8688/google-health-worker-mcp-V1/blob/main/src/providers/types.ts), [`src/providers/google/oauth.ts`](https://github.com/Ring8688/google-health-worker-mcp-V1/blob/main/src/providers/google/oauth.ts), and [`wrangler.toml.example`](https://github.com/Ring8688/google-health-worker-mcp-V1/blob/main/wrangler.toml.example).
- OpenAI Help Center: [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt-beta) (current workflow is product-dependent and must be revalidated before implementation).
