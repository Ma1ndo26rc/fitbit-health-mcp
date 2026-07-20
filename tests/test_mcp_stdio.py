import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "get_sleep",
    "get_steps",
    "get_heart_rate",
    "get_resting_heart_rate",
    "get_hrv",
    "get_health_summary",
}


async def inspect_server(parameters: StdioServerParameters, call_mock: bool) -> None:
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Fitbit Health"

            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
            for tool in tools.tools:
                assert set(tool.inputSchema["properties"]) == {"days"}
                days_schema = tool.inputSchema["properties"]["days"]
                assert days_schema["type"] == "integer"
                assert days_schema["enum"] == [14, 7, 3, 1]
                assert days_schema["default"] == 7

            if call_mock:
                result = await session.call_tool("get_steps", {"days": 3})
                assert result.isError is False
                assert result.structuredContent == {
                    "requested_days": 3,
                    "available_days": 1,
                    "data": [{"date": "2026-07-18", "steps": 2000}],
                    "missing_data": [],
                    "diagnostics": {},
                }
                assert json.loads(result.content[0].text) == result.structuredContent


def test_production_server_initializes_and_lists_six_tools_without_oauth() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fitbit_health.mcp_server"],
        cwd=PROJECT_ROOT,
    )

    asyncio.run(inspect_server(parameters, call_mock=False))


def test_fixture_server_returns_structured_json_from_mock_tool_call() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_ROOT / "tests" / "fixture_mcp_server.py")],
        cwd=PROJECT_ROOT,
    )

    asyncio.run(inspect_server(parameters, call_mock=True))
