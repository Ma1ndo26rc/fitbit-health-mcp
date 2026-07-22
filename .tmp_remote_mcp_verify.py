import asyncio
import json
import os
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ENDPOINT = "https://fitbit-health-mcp.onrender.com/mcp"


def emit(stage: str, **payload: Any) -> None:
    print(json.dumps({"stage": stage, **payload}, ensure_ascii=False, default=str))


def safe_error(exc: BaseException, token: str) -> str:
    messages: list[str] = []

    def collect(current: BaseException) -> None:
        nested = getattr(current, "exceptions", None)
        if nested:
            for child in nested:
                collect(child)
            return
        messages.append(f"{type(current).__name__}: {current}")

    collect(exc)
    message = " | ".join(messages) or f"{type(exc).__name__}: {exc}"
    return message.replace(token, "[redacted]") if token else message


async def main() -> None:
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token:
        emit("configuration", ok=False, classification="B", error="MCP_BEARER_TOKEN is unset")
        raise SystemExit(2)

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "fitbit-health-remote-verifier/1.0",
    }
    try:
        async with httpx.AsyncClient(headers=headers, timeout=60.0) as http_client:
            async with streamable_http_client(
                ENDPOINT,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                emit("transport", ok=True, endpoint=ENDPOINT, bearer_supplied=True)
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    emit(
                        "initialize",
                        ok=True,
                        protocol_version=initialized.protocolVersion,
                        server_name=initialized.serverInfo.name,
                    )

                    listed = await session.list_tools()
                    tool_names = [tool.name for tool in listed.tools]
                    emit("tools/list", ok=True, tools=tool_names)

                    called = await session.call_tool("get_steps", {"days": 1})
                    result = called.structuredContent
                    if result is None:
                        result = [
                            content.text
                            for content in called.content
                            if hasattr(content, "text")
                        ]
                    emit(
                        "tools/call",
                        ok=not called.isError,
                        tool="get_steps",
                        result=result,
                    )
    except BaseException as exc:
        message = safe_error(exc, token)
        lowered = message.lower()
        if "401" in lowered or "403" in lowered or "unauthorized" in lowered:
            classification = "B"
        elif "authentication" in lowered or "authorization is unavailable" in lowered:
            classification = "C"
        elif "google" in lowered or "health" in lowered:
            classification = "D"
        else:
            classification = "A"
        emit(
            "failure",
            ok=False,
            classification=classification,
            exception_type=type(exc).__name__,
            error=message,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
