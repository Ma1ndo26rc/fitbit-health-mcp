# Remote deployment reference

This document records the deployed v0.1.0 architecture. It is not a future migration plan.

## Runtime

- Platform: Render Python Web Service
- Process: `python -m fitbit_health.http_mcp_server`
- Bind address: `0.0.0.0:$PORT`
- MCP transport: Streamable HTTP at `/mcp`
- Topology: one service instance for one owner

The HTTP adapter and the stdio entry point reuse the same `create_server()`, six MCP tools, `HealthMCPService`, Google Health client, normalization, analytics, and diagnostics.

## Authorization boundaries

Two independent OAuth relationships are used:

1. ChatGPT authenticates to `/mcp` through the built-in MCP OAuth authorization code flow with PKCE. MCP access and refresh tokens are opaque and stored as digests in process memory.
2. The owner authorizes Google Health through `/auth/google` and `/oauth2/callback`. The resulting Google authorized-user credentials are written to the runtime `.private/token.json`.

The Google token is never issued to ChatGPT. Requested health data is returned through the Render MCP service to the connected ChatGPT conversation.

## Render secrets

Render must provide:

- `/etc/secrets/client_secret_render.json`: Google Web OAuth client configuration.
- `/etc/secrets/token.json`: optional seed used only when the writable runtime token is absent.
- Unique environment secrets for MCP owner authorization, Google bootstrap authorization, cookie signing, and the current legacy bearer compatibility path.

The exact environment-variable contract is documented in the root README and `render.yaml`.

## Restart behavior

Render Free does not provide persistent disk storage. A restart or redeploy can recreate the runtime filesystem. The service may restore the Google token from the Secret File seed, while all in-memory MCP access and refresh tokens are lost. The owner may need to update the Google seed, reauthorize Google, or reconnect ChatGPT depending on which credential expired.

## Release limitations

- Single-user, single-instance, and read-only.
- MCP OAuth token storage is in memory.
- The current startup requires the legacy static bearer compatibility secret in addition to MCP OAuth.
- A future security change should add `ENABLE_LEGACY_BEARER`, default it to disabled, and only construct the legacy verifier when explicitly enabled.
- The token endpoint does not yet enforce RFC 8707 `resource` on token and refresh requests, so full MCP 2025-11-25 authorization compliance is not claimed.

These limitations are documented rather than changed during v0.1.0 release preparation.
