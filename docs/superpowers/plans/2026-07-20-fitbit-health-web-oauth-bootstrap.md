# Fitbit Health Web OAuth Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a protected single-user Google Web OAuth bootstrap that persists the authorized-user token consumed by the existing remote MCP runtime.

**Architecture:** Add a focused `web_oauth.py` Starlette adapter for HTTP Basic, signed state, Google `Flow`, callback exchange, and safe responses. Attach its two routes to the existing FastMCP Starlette app while narrowing the existing bearer middleware to `/mcp`; persist credentials through the existing credential-storage boundary.

**Tech Stack:** Python 3.12, Starlette, google-auth-oauthlib, pytest, httpx ASGI transport.

## Global Constraints

- `/mcp` continues to require MCP bearer authentication.
- `/auth/google` uses independent HTTP Basic bootstrap protection.
- `/oauth2/callback` requires valid signed OAuth state and no MCP bearer.
- Use `Flow`, offline access, state validation, and `fetch_token()`.
- Reuse `FITBIT_HEALTH_TOKEN_PATH` and existing refresh/write-back behavior.
- Do not modify Google Health client, tool definitions, analytics, or add multi-user/database/Cloudflare/OAuth-provider/ChatGPT work.
- Never put a secret in a URL, response, or log.

---

### Task 1: Lock the HTTP and OAuth contract with failing tests

**Files:**
- Create: `tests/test_web_oauth.py`
- Modify: `tests/test_http_mcp_server.py`

**Interfaces:**
- Consumes: `create_http_app(...)`, Starlette ASGI app, `httpx.ASGITransport`.
- Produces: tests for Basic rejection, Google redirect, state cookie, callback validation, token persistence, leakage prevention, and `/mcp` bearer isolation.

- [ ] **Step 1: Write endpoint tests before production code**

Create a fake `Flow` only at the Google network boundary. Drive the real Starlette handlers with `httpx`, assert HTTP status/headers/cookies/files, and never assert merely that a mock exists.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_web_oauth.py tests/test_http_mcp_server.py -vv`

Expected: collection or endpoint failures because the Web OAuth adapter and routes do not exist.

### Task 2: Add safe token persistence and Web OAuth adapter

**Files:**
- Create: `src/fitbit_health/web_oauth.py`
- Modify: `src/fitbit_health/credential_storage.py`
- Test: `tests/test_web_oauth.py`

**Interfaces:**
- Consumes: `SCOPES`, `ensure_private_file()`, `Flow.from_client_secrets_file()`, Starlette `Request`.
- Produces: `WebOAuthBootstrap.routes()` and `write_authorized_user_token(token_path: Path, serialized: str) -> None`.

- [ ] **Step 1: Implement the minimum Basic and OAuth start behavior**

Decode HTTP Basic safely, require fixed username `bootstrap`, compare the configured password in constant time, construct `Flow`, request offline consent, store state in `request.session`, and return `302`.

- [ ] **Step 2: Implement the minimum callback behavior**

Consume and compare state, reject errors generically, call `fetch_token(authorization_response=...)`, serialize credentials, and persist them without logging values.

- [ ] **Step 3: Verify GREEN for OAuth tests**

Run: `python -m pytest tests/test_web_oauth.py -vv`

Expected: all Web OAuth tests pass.

### Task 3: Compose routes with the existing MCP application

**Files:**
- Modify: `src/fitbit_health/auth_boundary.py`
- Modify: `src/fitbit_health/http_mcp_server.py`
- Modify: `tests/test_http_mcp_server.py`

**Interfaces:**
- Consumes: `WebOAuthBootstrap`, `SessionMiddleware`, existing `create_server()` and bearer validator.
- Produces: `/mcp`, `/auth/google`, and `/oauth2/callback` with separate authentication rules.

- [ ] **Step 1: Restrict bearer enforcement to the MCP path**

Pass non-MCP HTTP paths through unchanged; retain current rejection behavior for `/mcp` and `/mcp/`.

- [ ] **Step 2: Attach OAuth routes and signed session middleware**

Use cookie name `fitbit_oauth_state`, `max_age=600`, `https_only=True`, and `same_site="lax"`.

- [ ] **Step 3: Wire required environment configuration in `main()`**

Read `OAUTH_BOOTSTRAP_PASSWORD`, `OAUTH_COOKIE_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, `FITBIT_HEALTH_CLIENT_SECRET_PATH`, and the resolved token path. Fail closed with names-only errors when a value is missing.

- [ ] **Step 4: Verify HTTP boundary GREEN**

Run: `python -m pytest tests/test_http_mcp_server.py tests/test_web_oauth.py -vv`

Expected: OAuth tests pass and existing MCP HTTP contract tests remain green.

### Task 4: Declare Render configuration and run final verification

**Files:**
- Modify: `render.yaml`
- Modify: `tests/test_render_config.py`

**Interfaces:**
- Consumes: environment names required by `http_mcp_server.main()`.
- Produces: Render blueprint declarations without embedded secret values.

- [ ] **Step 1: Add Render configuration tests and verify RED**

Assert that bootstrap password and redirect URI are operator-supplied and the cookie secret is generated or operator-supplied, with no literal credential values.

- [ ] **Step 2: Add the minimum blueprint variables**

Declare `OAUTH_BOOTSTRAP_PASSWORD`, `OAUTH_COOKIE_SECRET`, and `GOOGLE_OAUTH_REDIRECT_URI` without placing secrets in source.

- [ ] **Step 3: Run complete verification**

Run: `python -m pytest`

Expected: zero failures.

Run: `python -m py_compile src/fitbit_health/web_oauth.py src/fitbit_health/http_mcp_server.py src/fitbit_health/auth_boundary.py src/fitbit_health/credential_storage.py`

Expected: exit code 0.

Run: `git diff --check` and inspect `git diff --name-only`.

Expected: no whitespace errors and no forbidden modules modified.

