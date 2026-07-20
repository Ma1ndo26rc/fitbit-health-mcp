import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest

from fitbit_health import http_mcp_server
from fitbit_health.client import FetchResult
from fitbit_health.http_mcp_server import create_http_app
from fitbit_health.mcp_tools import HealthMCPService


EXPECTED_TOOLS = {
    "get_sleep",
    "get_steps",
    "get_heart_rate",
    "get_resting_heart_rate",
    "get_hrv",
    "get_health_summary",
}

FAKE_BEARER_TOKEN = "phase-2b-test-token"


def set_web_oauth_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "FITBIT_HEALTH_CLIENT_SECRET_PATH",
        "/etc/secrets/client_secret_render.json",
    )
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "https://fitbit-health-mcp.onrender.com/oauth2/callback",
    )
    monkeypatch.setenv("OAUTH_BOOTSTRAP_PASSWORD", "bootstrap-password")
    monkeypatch.setenv("OAUTH_COOKIE_SECRET", "cookie-signing-secret")


class FakeBearerTokenValidator:
    def __init__(self, accepted_token: str) -> None:
        self.accepted_token = accepted_token
        self.seen_tokens: list[str] = []

    async def validate(self, token: str) -> bool:
        self.seen_tokens.append(token)
        return token == self.accepted_token


@pytest.fixture
def fake_service_factory() -> tuple[Mock, Mock]:
    fake_client_factory = Mock(
        side_effect=AssertionError("HTTP contract discovery must not call Google Health")
    )
    service = HealthMCPService(
        Path("."),
        client_factory=fake_client_factory,
    )
    return Mock(return_value=service), fake_client_factory


@asynccontextmanager
async def http_session(service_factory: Mock):
    token_validator = FakeBearerTokenValidator(FAKE_BEARER_TOKEN)
    app = create_http_app(
        service_factory=service_factory,
        token_validator=token_validator,
    )
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": f"Bearer {FAKE_BEARER_TOKEN}"},
        ) as http_client:
            async with streamable_http_client(
                "http://127.0.0.1:8000/mcp",
                http_client=http_client,
                terminate_on_close=False,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    yield session


async def inspect_http_contract(service_factory: Mock) -> None:
    async with http_session(service_factory) as session:
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


def test_streamable_http_initialize_and_tools_list_match_stdio_contract(
    fake_service_factory: tuple[Mock, Mock],
) -> None:
    service_factory, fake_client_factory = fake_service_factory

    asyncio.run(inspect_http_contract(service_factory))

    service_factory.assert_not_called()
    fake_client_factory.assert_not_called()


def test_unauthorized_request_is_rejected() -> None:
    service_factory = Mock()
    app = create_http_app(
        service_factory=service_factory,
        token_validator=FakeBearerTokenValidator(FAKE_BEARER_TOKEN),
    )

    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post("/mcp", json={})

    response = asyncio.run(send_request())

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    service_factory.assert_not_called()


def test_invalid_fake_bearer_token_is_rejected() -> None:
    service_factory = Mock()
    token_validator = FakeBearerTokenValidator(FAKE_BEARER_TOKEN)
    app = create_http_app(
        service_factory=service_factory,
        token_validator=token_validator,
    )

    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": "Bearer wrong-token"},
        ) as client:
            return await client.post("/mcp", json={})

    response = asyncio.run(send_request())

    assert response.status_code == 401
    assert token_validator.seen_tokens == ["wrong-token"]
    service_factory.assert_not_called()


def test_google_credentials_are_not_accessed_before_auth(monkeypatch) -> None:
    service_constructor = Mock(
        side_effect=AssertionError("HealthMCPService must not be created before auth")
    )
    credential_loader = Mock(
        side_effect=AssertionError("Google credentials must not be loaded before auth")
    )
    monkeypatch.setattr(
        "fitbit_health.credential_storage.HealthMCPService",
        service_constructor,
    )
    monkeypatch.setattr(
        "fitbit_health.mcp_tools.load_saved_credentials",
        credential_loader,
    )
    app = create_http_app(
        token_validator=FakeBearerTokenValidator(FAKE_BEARER_TOKEN),
    )

    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
        ) as client:
            return await client.post("/mcp", json={})

    response = asyncio.run(send_request())

    assert response.status_code == 401
    service_constructor.assert_not_called()
    credential_loader.assert_not_called()


def test_http_app_exposes_mcp_at_expected_path(
    fake_service_factory: tuple[Mock, Mock],
) -> None:
    service_factory, _ = fake_service_factory

    app = create_http_app(
        service_factory=service_factory,
        token_validator=FakeBearerTokenValidator(FAKE_BEARER_TOKEN),
    )

    assert [route.path for route in app.routes] == ["/mcp"]


def test_external_http_client_can_call_tool_without_real_google() -> None:
    today = date(2026, 7, 20)
    fake_client = Mock()
    fake_client.fetch_all.return_value = FetchResult(
        "steps",
        [{
            "steps": {
                "interval": {
                    "civilEndTime": {
                        "date": {"year": 2026, "month": 7, "day": 20}
                    }
                },
                "count": "2000",
            }
        }],
    )
    service = HealthMCPService(
        Path("."),
        client_factory=lambda: fake_client,
        today_factory=lambda: today,
    )
    service_factory = Mock(return_value=service)

    async def call_tool():
        async with http_session(service_factory) as session:
            await session.initialize()
            return await session.call_tool("get_steps", {"days": 1})

    result = asyncio.run(call_tool())

    assert result.isError is False
    assert result.structuredContent == {
        "requested_days": 1,
        "available_days": 1,
        "data": [{"date": "2026-07-20", "steps": 2000}],
        "missing_data": [],
        "diagnostics": {},
    }
    service_factory.assert_called_once_with()
    fake_client.fetch_all.assert_called_once_with("steps", today)


def test_main_runs_existing_server_with_streamable_http_transport(
    monkeypatch,
) -> None:
    app = Mock()
    app_factory = Mock(return_value=app)
    uvicorn_run = Mock()
    monkeypatch.setattr(http_mcp_server, "create_http_app", app_factory)
    monkeypatch.setattr(http_mcp_server.uvicorn, "run", uvicorn_run)
    monkeypatch.setenv("MCP_BEARER_TOKEN", FAKE_BEARER_TOKEN)
    set_web_oauth_environment(monkeypatch)

    http_mcp_server.main()

    app_factory.assert_called_once()
    validator = app_factory.call_args.kwargs["token_validator"]
    assert asyncio.run(validator.validate(FAKE_BEARER_TOKEN)) is True
    oauth_bootstrap = app_factory.call_args.kwargs["oauth_bootstrap"]
    assert oauth_bootstrap.client_path == Path(
        "/etc/secrets/client_secret_render.json"
    )
    assert oauth_bootstrap.redirect_uri == (
        "https://fitbit-health-mcp.onrender.com/oauth2/callback"
    )
    uvicorn_run.assert_called_once_with(app, host="127.0.0.1", port=8000)


def test_main_maps_render_runtime_environment_to_fastmcp(
    monkeypatch,
) -> None:
    app = Mock()
    app_factory = Mock(return_value=app)
    uvicorn_run = Mock()
    monkeypatch.setattr(http_mcp_server, "create_http_app", app_factory)
    monkeypatch.setattr(http_mcp_server.uvicorn, "run", uvicorn_run)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "fitbit-health-mcp.onrender.com")
    monkeypatch.setenv("MCP_BEARER_TOKEN", FAKE_BEARER_TOKEN)
    set_web_oauth_environment(monkeypatch)

    http_mcp_server.main()

    assert app_factory.call_args.kwargs["external_hostname"] == (
        "fitbit-health-mcp.onrender.com"
    )
    uvicorn_run.assert_called_once_with(app, host="0.0.0.0", port=10000)


def test_main_refuses_to_start_without_bearer_token(monkeypatch) -> None:
    app_factory = Mock()
    monkeypatch.setattr(http_mcp_server, "create_http_app", app_factory)
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="MCP_BEARER_TOKEN"):
        http_mcp_server.main()

    app_factory.assert_not_called()


@pytest.mark.parametrize(
    "missing_name",
    [
        "FITBIT_HEALTH_CLIENT_SECRET_PATH",
        "GOOGLE_OAUTH_REDIRECT_URI",
        "OAUTH_BOOTSTRAP_PASSWORD",
        "OAUTH_COOKIE_SECRET",
    ],
)
def test_main_refuses_to_start_without_web_oauth_configuration(
    monkeypatch,
    missing_name: str,
) -> None:
    app_factory = Mock()
    monkeypatch.setattr(http_mcp_server, "create_http_app", app_factory)
    monkeypatch.setenv("MCP_BEARER_TOKEN", FAKE_BEARER_TOKEN)
    set_web_oauth_environment(monkeypatch)
    monkeypatch.delenv(missing_name)

    with pytest.raises(RuntimeError, match=missing_name):
        http_mcp_server.main()

    app_factory.assert_not_called()
