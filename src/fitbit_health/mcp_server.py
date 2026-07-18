from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.fastmcp import FastMCP

from fitbit_health.fetch_window import DEFAULT_FETCH_DAYS, FetchDays
from fitbit_health.mcp_tools import HealthMCPService


class ToolEnvelope(TypedDict):
    requested_days: int
    available_days: int
    data: list[dict[str, Any]] | dict[str, Any]
    missing_data: list[str]
    diagnostics: dict[str, Any]


def create_server(
    service_factory: Callable[[], HealthMCPService] | None = None,
) -> FastMCP:
    """Create the local stdio server without authenticating or fetching data."""
    factory = service_factory or (lambda: HealthMCPService(Path.cwd()))
    service: HealthMCPService | None = None

    def get_service() -> HealthMCPService:
        nonlocal service
        if service is None:
            service = factory()
        return service

    server = FastMCP("Fitbit Health", json_response=True)

    @server.tool(structured_output=True)
    def get_sleep(days: FetchDays = DEFAULT_FETCH_DAYS) -> ToolEnvelope:
        """Get normalized daily sleep data for the requested number of days."""
        return get_service().get_sleep(days)

    @server.tool(structured_output=True)
    def get_steps(days: FetchDays = DEFAULT_FETCH_DAYS) -> ToolEnvelope:
        """Get normalized daily step counts for the requested number of days."""
        return get_service().get_steps(days)

    @server.tool(structured_output=True)
    def get_heart_rate(days: FetchDays = DEFAULT_FETCH_DAYS) -> ToolEnvelope:
        """Get daily average heart rate for the requested number of days."""
        return get_service().get_heart_rate(days)

    @server.tool(structured_output=True)
    def get_resting_heart_rate(days: FetchDays = DEFAULT_FETCH_DAYS) -> ToolEnvelope:
        """Get daily resting heart rate for the requested number of days."""
        return get_service().get_resting_heart_rate(days)

    @server.tool(structured_output=True)
    def get_hrv(days: FetchDays = DEFAULT_FETCH_DAYS) -> ToolEnvelope:
        """Get daily HRV RMSSD for the requested number of days."""
        return get_service().get_hrv(days)

    @server.tool(structured_output=True)
    def get_health_summary(days: FetchDays = DEFAULT_FETCH_DAYS) -> ToolEnvelope:
        """Get the existing multi-metric health analysis as structured JSON."""
        return get_service().get_health_summary(days)

    return server


def main() -> None:
    """Run the Fitbit Health MCP server over standard input/output."""
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
