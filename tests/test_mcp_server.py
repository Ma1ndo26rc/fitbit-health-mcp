import asyncio
from unittest.mock import Mock

from fitbit_health.mcp_server import create_server


EXPECTED_TOOLS = {
    "get_sleep",
    "get_steps",
    "get_heart_rate",
    "get_resting_heart_rate",
    "get_hrv",
    "get_health_summary",
}


class FakeService:
    def _result(self, days: int) -> dict:
        return {
            "requested_days": days,
            "available_days": 0,
            "data": [],
            "missing_data": [],
            "diagnostics": {},
        }

    def get_sleep(self, days: int = 7) -> dict:
        return self._result(days)

    def get_steps(self, days: int = 7) -> dict:
        return self._result(days)

    def get_heart_rate(self, days: int = 7) -> dict:
        return self._result(days)

    def get_resting_heart_rate(self, days: int = 7) -> dict:
        return self._result(days)

    def get_hrv(self, days: int = 7) -> dict:
        return self._result(days)

    def get_health_summary(self, days: int = 7) -> dict:
        result = self._result(days)
        result["data"] = {}
        return result


def test_create_server_is_lazy_and_registers_exactly_six_tools() -> None:
    service_factory = Mock(return_value=FakeService())

    server = create_server(service_factory=service_factory)
    tools = asyncio.run(server.list_tools())

    service_factory.assert_not_called()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        days_schema = tool.inputSchema["properties"]["days"]
        assert days_schema["type"] == "integer"
        assert days_schema["default"] == 7
        assert tool.description


def test_registered_tool_delegates_and_returns_structured_json() -> None:
    service = FakeService()
    service.get_steps = Mock(return_value=service._result(3))
    server = create_server(service_factory=lambda: service)

    content, structured = asyncio.run(
        server.call_tool("get_steps", {"days": 3})
    )

    service.get_steps.assert_called_once_with(3)
    assert structured == {
        "requested_days": 3,
        "available_days": 0,
        "data": [],
        "missing_data": [],
        "diagnostics": {},
    }
    assert '"requested_days": 3' in content[0].text
