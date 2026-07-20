import asyncio
import json
from unittest.mock import Mock

import pytest

from fitbit_health.fetch_window import FETCH_DAYS_ERROR
from fitbit_health.mcp_server import create_server


EXPECTED_TOOLS = {
    "get_sleep",
    "get_steps",
    "get_heart_rate",
    "get_resting_heart_rate",
    "get_hrv",
    "get_health_summary",
}
EXPECTED_ENVELOPE_FIELDS = {
    "requested_days",
    "available_days",
    "data",
    "missing_data",
    "diagnostics",
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
        assert days_schema["enum"] == [14, 7, 3, 1]
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


@pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
def test_all_registered_tools_preserve_default_and_envelope_contract(
    tool_name: str,
) -> None:
    server = create_server(service_factory=FakeService)

    content, structured = asyncio.run(server.call_tool(tool_name, {}))

    assert set(structured) == EXPECTED_ENVELOPE_FIELDS
    assert structured["requested_days"] == 7
    assert json.loads(content[0].text) == structured


@pytest.mark.parametrize("days", [2, True, "7"])
def test_registered_tool_delegates_invalid_days_to_service_envelope(days) -> None:
    service = FakeService()
    received_days = []

    def validation_envelope(value):
        received_days.append(value)
        return {
            "requested_days": (
                value if isinstance(value, int) and not isinstance(value, bool) else 0
            ),
            "available_days": 0,
            "data": [],
            "missing_data": [],
            "diagnostics": {"validation": FETCH_DAYS_ERROR},
        }

    service.get_steps = Mock(side_effect=validation_envelope)
    server = create_server(service_factory=lambda: service)

    content, structured = asyncio.run(
        server.call_tool("get_steps", {"days": days})
    )

    service.get_steps.assert_called_once()
    assert received_days[0] is days
    assert structured == {
        "requested_days": (
            days if isinstance(days, int) and not isinstance(days, bool) else 0
        ),
        "available_days": 0,
        "data": [],
        "missing_data": [],
        "diagnostics": {"validation": FETCH_DAYS_ERROR},
    }
    assert '"validation"' in content[0].text
