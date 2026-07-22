import asyncio
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.settings import AuthSettings
import pytest

from fitbit_health import http_mcp_server
from fitbit_health.client import FetchResult
from fitbit_health.http_mcp_server import create_http_app
from fitbit_health.mcp_oauth import (
    AuthorizationCodeStore,
    FixedPublicClientRegistry,
    MCPOAuthAuthorization,
    MCPOAuthMetadata,
    MCPOAuthTokenEndpoint,
    SingleUserAuthorizationProvider,
)
from fitbit_health.mcp_resource_auth import (
    CompositeTokenVerifier,
    LegacyStaticTokenVerifier,
    OpaqueAccessTokenVerifier,
)
from fitbit_health.mcp_token_store import OpaqueTokenStore
from fitbit_health.mcp_tools import HealthMCPService
from fitbit_health.web_oauth import WebOAuthBootstrap


EXPECTED_TOOLS = {
    "get_sleep",
    "get_steps",
    "get_heart_rate",
    "get_resting_heart_rate",
    "get_hrv",
    "get_health_summary",
}

FAKE_BEARER_TOKEN = "phase-2b-test-token"
MCP_OAUTH_ISSUER_URL = "https://fitbit-health-mcp.onrender.com"
MCP_OAUTH_RESOURCE_URL = f"{MCP_OAUTH_ISSUER_URL}/mcp"
MCP_OAUTH_CLIENT_ID = "chatgpt-public-client"
MCP_OAUTH_REDIRECT_URI = "https://chatgpt.com/connector/oauth/test-callback"
MCP_OAUTH_OWNER_PASSWORD = "owner-password"


def make_auth_settings() -> AuthSettings:
    return AuthSettings(
        issuer_url=MCP_OAUTH_ISSUER_URL,
        resource_server_url=MCP_OAUTH_RESOURCE_URL,
        required_scopes=["health:read"],
    )


def make_composite_verifier(token_store: OpaqueTokenStore):
    return CompositeTokenVerifier(
        verifiers=(
            LegacyStaticTokenVerifier(
                accepted_token=FAKE_BEARER_TOKEN,
                resource_url=MCP_OAUTH_RESOURCE_URL,
            ),
            OpaqueAccessTokenVerifier(
                token_store=token_store,
                resource_url=MCP_OAUTH_RESOURCE_URL,
            ),
        )
    )


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


def set_mcp_oauth_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCP_OAUTH_ISSUER_URL", MCP_OAUTH_ISSUER_URL)
    monkeypatch.setenv("MCP_OAUTH_RESOURCE_URL", MCP_OAUTH_RESOURCE_URL)
    monkeypatch.setenv("MCP_OAUTH_CLIENT_ID", MCP_OAUTH_CLIENT_ID)
    monkeypatch.setenv("MCP_OAUTH_REDIRECT_URI", MCP_OAUTH_REDIRECT_URI)
    monkeypatch.setenv("MCP_OAUTH_OWNER_PASSWORD", MCP_OAUTH_OWNER_PASSWORD)


def make_mcp_oauth_components(
    token_store: OpaqueTokenStore | None = None,
) -> tuple[
    MCPOAuthAuthorization,
    MCPOAuthTokenEndpoint,
]:
    registry = FixedPublicClientRegistry(
        client_id=MCP_OAUTH_CLIENT_ID,
        redirect_uri=MCP_OAUTH_REDIRECT_URI,
    )
    provider = SingleUserAuthorizationProvider(
        registry=registry,
        code_store=AuthorizationCodeStore(),
        token_store=token_store or OpaqueTokenStore(),
        resource_url=MCP_OAUTH_RESOURCE_URL,
    )
    return (
        MCPOAuthAuthorization(
            provider=provider,
            owner_password=MCP_OAUTH_OWNER_PASSWORD,
        ),
        MCPOAuthTokenEndpoint(provider=provider),
    )


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
async def http_session(
    service_factory: Mock,
    *,
    bearer_token: str = FAKE_BEARER_TOKEN,
    token_store: OpaqueTokenStore | None = None,
):
    token_store = token_store or OpaqueTokenStore()
    app = create_http_app(
        service_factory=service_factory,
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
    )
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
            headers={"Authorization": f"Bearer {bearer_token}"},
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
    token_store = OpaqueTokenStore()
    app = create_http_app(
        service_factory=service_factory,
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
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
    assert response.headers["WWW-Authenticate"].startswith("Bearer ")
    assert response.json()["error"] == "invalid_token"
    service_factory.assert_not_called()


def test_invalid_fake_bearer_token_is_rejected() -> None:
    service_factory = Mock()
    token_store = OpaqueTokenStore()
    app = create_http_app(
        service_factory=service_factory,
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
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
    assert response.json()["error"] == "invalid_token"
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
    token_store = OpaqueTokenStore()
    app = create_http_app(
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
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

    token_store = OpaqueTokenStore()
    app = create_http_app(
        service_factory=service_factory,
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
    )

    assert [route.path for route in app.routes] == [
        "/mcp",
        "/.well-known/oauth-protected-resource/mcp",
    ]


def test_http_app_mounts_public_oauth_metadata_without_changing_mcp_auth(
    fake_service_factory: tuple[Mock, Mock],
) -> None:
    service_factory, fake_client_factory = fake_service_factory
    token_store = OpaqueTokenStore()
    metadata = MCPOAuthMetadata(
        issuer_url=MCP_OAUTH_ISSUER_URL,
        resource_url=MCP_OAUTH_RESOURCE_URL,
    )
    app = create_http_app(
        service_factory=service_factory,
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
        mcp_oauth_metadata=metadata,
    )

    async def inspect_routes() -> tuple[
        httpx.Response,
        httpx.Response,
        httpx.Response,
    ]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=MCP_OAUTH_ISSUER_URL,
        ) as client:
            metadata_response = await client.get(
                "/.well-known/oauth-protected-resource"
            )
            native_metadata_response = await client.get(
                "/.well-known/oauth-protected-resource/mcp"
            )
            mcp_response = await client.post("/mcp", json={})
            return metadata_response, native_metadata_response, mcp_response

    metadata_response, native_metadata_response, mcp_response = asyncio.run(
        inspect_routes()
    )

    assert [route.path for route in app.routes] == [
        "/mcp",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    ]
    assert metadata_response.status_code == 200
    assert native_metadata_response.status_code == 200
    assert native_metadata_response.json()["resource"] == MCP_OAUTH_RESOURCE_URL
    assert mcp_response.status_code == 401
    assert mcp_response.headers["WWW-Authenticate"].startswith("Bearer ")
    service_factory.assert_not_called()
    fake_client_factory.assert_not_called()


def test_native_mcp_auth_does_not_block_google_oauth_routes() -> None:
    token_store = OpaqueTokenStore()
    oauth_bootstrap = WebOAuthBootstrap(
        client_path=Path("client_secret.json"),
        token_path=Path("token.json"),
        redirect_uri=f"{MCP_OAUTH_ISSUER_URL}/oauth2/callback",
        bootstrap_password="bootstrap-password",
        cookie_secret="cookie-secret",
    )
    app = create_http_app(
        service_factory=Mock(),
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
        oauth_bootstrap=oauth_bootstrap,
    )

    async def request_google_route():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=MCP_OAUTH_ISSUER_URL,
        ) as client:
            return await client.get("/auth/google")

    response = asyncio.run(request_google_route())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Basic realm="Fitbit Health OAuth Bootstrap"'
    )


def test_native_auth_rejects_wrong_resource_and_missing_scope() -> None:
    access_tokens = iter(("wrong-resource-token", "missing-scope-token"))
    refresh_tokens = iter(("refresh-1", "refresh-2"))
    token_store = OpaqueTokenStore(
        access_token_factory=lambda: next(access_tokens),
        refresh_token_factory=lambda: next(refresh_tokens),
    )

    async def make_requests():
        wrong_resource = await token_store.issue(
            client_id=MCP_OAUTH_CLIENT_ID,
            scopes=("health:read",),
            resource=f"{MCP_OAUTH_ISSUER_URL}/other",
            subject="single-owner",
        )
        missing_scope = await token_store.issue(
            client_id=MCP_OAUTH_CLIENT_ID,
            scopes=("profile",),
            resource=MCP_OAUTH_RESOURCE_URL,
            subject="single-owner",
        )
        app = create_http_app(
            service_factory=Mock(),
            token_verifier=make_composite_verifier(token_store),
            auth_settings=make_auth_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=MCP_OAUTH_ISSUER_URL,
        ) as client:
            wrong_resource_response = await client.post(
                "/mcp",
                json={},
                headers={
                    "Authorization": f"Bearer {wrong_resource.access_token}"
                },
            )
            missing_scope_response = await client.post(
                "/mcp",
                json={},
                headers={
                    "Authorization": f"Bearer {missing_scope.access_token}"
                },
            )
        return wrong_resource_response, missing_scope_response

    wrong_resource, missing_scope = asyncio.run(make_requests())

    assert wrong_resource.status_code == 401
    assert wrong_resource.json()["error"] == "invalid_token"
    assert missing_scope.status_code == 403
    assert missing_scope.json()["error"] == "insufficient_scope"
    assert "Required scope: health:read" in missing_scope.json()[
        "error_description"
    ]


def test_oauth_access_token_initializes_lists_tools_and_calls_get_steps() -> None:
    today = date(2026, 7, 22)
    fake_client = Mock()
    fake_client.fetch_all.return_value = FetchResult(
        "steps",
        [{
            "steps": {
                "interval": {
                    "civilEndTime": {
                        "date": {"year": 2026, "month": 7, "day": 22}
                    }
                },
                "count": "3456",
            }
        }],
    )
    service = HealthMCPService(
        Path("."),
        client_factory=lambda: fake_client,
        today_factory=lambda: today,
    )
    service_factory = Mock(return_value=service)
    token_store = OpaqueTokenStore(
        access_token_factory=lambda: "oauth-mcp-access-token",
        refresh_token_factory=lambda: "oauth-mcp-refresh-token",
    )

    async def exercise_protocol():
        pair = await token_store.issue(
            client_id=MCP_OAUTH_CLIENT_ID,
            scopes=("health:read",),
            resource=MCP_OAUTH_RESOURCE_URL,
            subject="single-owner",
        )
        app = create_http_app(
            service_factory=service_factory,
            token_verifier=make_composite_verifier(token_store),
            auth_settings=make_auth_settings(),
        )
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:8000",
                headers={"Authorization": f"Bearer {pair.access_token}"},
            ) as http_client:
                async with streamable_http_client(
                    "http://127.0.0.1:8000/mcp",
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        tools = await session.list_tools()
                        result = await session.call_tool("get_steps", {"days": 1})
        return initialized, tools, result

    initialized, tools, result = asyncio.run(exercise_protocol())

    assert initialized.serverInfo.name == "Fitbit Health"
    assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
    assert result.isError is False
    assert result.structuredContent["data"] == [
        {"date": "2026-07-22", "steps": 3456}
    ]
    service_factory.assert_called_once_with()


def test_http_app_mounts_oauth_routes_and_keeps_mcp_bearer_auth(
    fake_service_factory: tuple[Mock, Mock],
) -> None:
    service_factory, fake_client_factory = fake_service_factory
    token_store = OpaqueTokenStore()
    authorization, token_endpoint = make_mcp_oauth_components(token_store)
    app = create_http_app(
        service_factory=service_factory,
        token_verifier=make_composite_verifier(token_store),
        auth_settings=make_auth_settings(),
        mcp_oauth_authorization=authorization,
        mcp_oauth_token_endpoint=token_endpoint,
    )

    async def inspect_routes():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=MCP_OAUTH_ISSUER_URL,
        ) as client:
            authorize = await client.get("/oauth/authorize")
            token_get = await client.get("/oauth/token")
            token_post = await client.post("/oauth/token", data={})
            mcp = await client.post("/mcp", json={})
            return authorize, token_get, token_post, mcp

    authorize, token_get, token_post, mcp = asyncio.run(inspect_routes())

    assert "/oauth/authorize" in [route.path for route in app.routes]
    assert authorize.status_code == 401
    assert authorize.headers["WWW-Authenticate"].startswith("Basic ")
    assert token_get.status_code == 405
    assert token_post.status_code == 401
    assert token_post.json()["error"] == "unauthorized_client"
    assert mcp.status_code == 401
    assert mcp.headers["WWW-Authenticate"].startswith("Bearer ")
    service_factory.assert_not_called()
    fake_client_factory.assert_not_called()


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
    set_mcp_oauth_environment(monkeypatch)

    http_mcp_server.main()

    app_factory.assert_called_once()
    verifier = app_factory.call_args.kwargs["token_verifier"]
    assert asyncio.run(verifier.verify_token(FAKE_BEARER_TOKEN)) is not None
    assert asyncio.run(verifier.verify_token("wrong-token")) is None
    auth_settings = app_factory.call_args.kwargs["auth_settings"]
    assert str(auth_settings.issuer_url).rstrip("/") == MCP_OAUTH_ISSUER_URL
    assert str(auth_settings.resource_server_url) == MCP_OAUTH_RESOURCE_URL
    assert auth_settings.required_scopes == ["health:read"]
    oauth_bootstrap = app_factory.call_args.kwargs["oauth_bootstrap"]
    assert oauth_bootstrap.client_path == Path(
        "/etc/secrets/client_secret_render.json"
    )
    assert oauth_bootstrap.redirect_uri == (
        "https://fitbit-health-mcp.onrender.com/oauth2/callback"
    )
    metadata = app_factory.call_args.kwargs["mcp_oauth_metadata"]
    assert metadata.issuer_url == MCP_OAUTH_ISSUER_URL
    assert metadata.resource_url == MCP_OAUTH_RESOURCE_URL
    authorization = app_factory.call_args.kwargs["mcp_oauth_authorization"]
    assert [route.path for route in authorization.routes()] == ["/oauth/authorize"]
    token_endpoint = app_factory.call_args.kwargs["mcp_oauth_token_endpoint"]
    assert [route.path for route in token_endpoint.routes()] == ["/oauth/token"]
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
    set_mcp_oauth_environment(monkeypatch)

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
    set_mcp_oauth_environment(monkeypatch)
    monkeypatch.delenv(missing_name)

    with pytest.raises(RuntimeError, match=missing_name):
        http_mcp_server.main()

    app_factory.assert_not_called()


@pytest.mark.parametrize(
    "missing_name",
    [
        "MCP_OAUTH_CLIENT_ID",
        "MCP_OAUTH_REDIRECT_URI",
        "MCP_OAUTH_OWNER_PASSWORD",
    ],
)
def test_main_refuses_to_start_without_mcp_authorization_configuration(
    monkeypatch,
    missing_name: str,
) -> None:
    app_factory = Mock()
    monkeypatch.setattr(http_mcp_server, "create_http_app", app_factory)
    monkeypatch.setenv("MCP_BEARER_TOKEN", FAKE_BEARER_TOKEN)
    set_web_oauth_environment(monkeypatch)
    set_mcp_oauth_environment(monkeypatch)
    monkeypatch.delenv(missing_name)

    with pytest.raises(RuntimeError, match=missing_name):
        http_mcp_server.main()

    app_factory.assert_not_called()
