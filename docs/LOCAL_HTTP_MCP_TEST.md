# Local Streamable HTTP MCP test

This check exercises the HTTP transport with synthetic data. It does not read OAuth credentials, call Google Health, or expose a public endpoint.

## Install

From the repository root:

```powershell
python -m pip install -e ".[test]"
```

## Run the automated transport tests

```powershell
python -m pytest -q tests/test_http_mcp_server.py
```

The tests construct the same Streamable HTTP application used by the remote entry point, inject a fake health service, and verify initialization, tool discovery, authentication boundaries, OAuth metadata, and tool calls.

## What the test proves

- The Streamable HTTP route is `/mcp`.
- HTTP and stdio register the same six tools and schemas.
- Authentication is evaluated before the injected health service is used.
- OAuth discovery and the owner-protected authorization routes are present.
- Synthetic tool results preserve the structured MCP envelope.

It does not prove public reachability, Render configuration, live Google credentials, real health-data access, or end-to-end ChatGPT connectivity.
