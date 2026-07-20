# Local Streamable HTTP MCP Test

This guide verifies the local Fitbit Health MCP endpoint with an MCP client. It is not a deployment guide and does not configure ChatGPT, Cloudflare, a database, or a new OAuth flow.

## Preconditions

- Run commands from `E:\CodeX_Lab`.
- Use the project Python environment (`D:\anaconda\python.exe`).
- Port `8000` must be available on the local machine.
- The server binds to loopback by default. Do not expose it to another machine or the public internet.

## Start the HTTP MCP server

```powershell
D:\anaconda\python.exe -m fitbit_health.http_mcp_server
```

The command reuses `fitbit_health.mcp_server.create_server()` and starts FastMCP's native `streamable-http` transport. It does not create a second tool registry.

Endpoint:

```text
http://127.0.0.1:8000/mcp
```

`/mcp` is an MCP protocol endpoint, not a web page. Opening it directly in a browser is not a valid initialize test; use an MCP client that sends protocol messages.

Stop the local server with `Ctrl+C` when verification is complete.

## Verify with a Python MCP client

Save the following example outside the repository or run it from an interactive Python session while the server is running:

```python
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ENDPOINT = "http://127.0.0.1:8000/mcp"


async def main() -> None:
    async with streamable_http_client(ENDPOINT) as (read, write, _):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            print("server:", initialized.serverInfo.name)

            tools = await session.list_tools()
            print("tools:", [tool.name for tool in tools.tools])


asyncio.run(main())
```

Expected initialize result:

```text
server: Fitbit Health
```

Expected `tools/list` names, ignoring display order:

```text
get_sleep
get_steps
get_heart_rate
get_resting_heart_rate
get_hrv
get_health_summary
```

Each tool should expose one `days` input with:

```text
type: integer
enum: [14, 7, 3, 1]
default: 7
```

`initialize` and `tools/list` do not construct `HealthMCPService`, start OAuth, or call Google Health.

## Verify a tool call

Calling a tool on the normal local server uses the existing `.private/token.json` and Google Health client. Only do this manually when using the authorized local account is intended.

Add this inside the initialized `ClientSession` block:

```python
result = await session.call_tool("get_steps", {"days": 1})
print("is_error:", result.isError)
print("result:", result.structuredContent)
```

The structured result must contain:

```text
requested_days
available_days
data
missing_data
diagnostics
```

For a protocol-level tool-call check that must not contact Google Health, use the automated fake-client test instead:

```powershell
D:\anaconda\python.exe -m pytest -q tests\test_http_mcp_server.py
```

That test creates the same Streamable HTTP app through `create_server()`, connects with the MCP Streamable HTTP client, calls `get_steps`, and injects a fake `GoogleHealthClient` result. It does not read OAuth credentials or make a network request to Google.

## What this proves

- FastMCP starts with `transport="streamable-http"`.
- The local MCP route is `/mcp`.
- An MCP client can complete `initialize`, `tools/list`, and a fake-backed `tools/call` over HTTP.
- HTTP and stdio use the same six registered tools and schemas.

This does not prove public reachability, TLS, caller authentication, OAuth metadata for ChatGPT, production hosting, or health-data privacy controls.
