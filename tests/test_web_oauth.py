import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import httpx

from fitbit_health.http_mcp_server import create_http_app
from fitbit_health.web_oauth import WebOAuthBootstrap


BOOTSTRAP_PASSWORD = "phase-3b-bootstrap-password"
COOKIE_SECRET = "phase-3b-cookie-signing-secret"
REDIRECT_URI = "https://fitbit-health.example/oauth2/callback"
GOOGLE_AUTHORIZATION_URL = (
    "https://accounts.google.example/authorize?access_type=offline"
)
OAUTH_STATE = "phase-3b-oauth-state"


class AllowOnlyMCPToken:
    async def validate(self, token: str) -> bool:
        return token == "mcp-only-token"


def test_runtime_declares_signed_session_dependency() -> None:
    repository_root = Path(__file__).parents[1]
    pyproject = (repository_root / "pyproject.toml").read_text(encoding="utf-8")

    assert '"itsdangerous>=2.2,<3"' in pyproject


def make_app(
    tmp_path: Path,
    flow_factory: Mock,
):
    client_path = tmp_path / "client_secret_web.json"
    client_path.write_text(
        json.dumps({"web": {"client_id": "web-client-id"}}),
        encoding="utf-8",
    )
    token_path = tmp_path / "runtime" / ".private" / "token.json"
    bootstrap = WebOAuthBootstrap(
        client_path=client_path,
        token_path=token_path,
        redirect_uri=REDIRECT_URI,
        bootstrap_password=BOOTSTRAP_PASSWORD,
        cookie_secret=COOKIE_SECRET,
        flow_factory=flow_factory,
    )
    app = create_http_app(
        service_factory=Mock(),
        token_validator=AllowOnlyMCPToken(),
        oauth_bootstrap=bootstrap,
    )
    return app, token_path


def basic_auth() -> httpx.BasicAuth:
    return httpx.BasicAuth("bootstrap", BOOTSTRAP_PASSWORD)


async def get(app, path: str, *, auth=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://fitbit-health.example",
    ) as client:
        return await client.get(path, auth=auth, follow_redirects=False)


def test_oauth_start_rejects_missing_basic_auth(
    tmp_path: Path,
) -> None:
    flow_factory = Mock()
    app, _ = make_app(tmp_path, flow_factory)

    response = asyncio.run(get(app, "/auth/google"))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")
    assert BOOTSTRAP_PASSWORD not in response.text
    flow_factory.assert_not_called()


def test_oauth_start_redirects_to_google_with_offline_access(
    tmp_path: Path,
) -> None:
    start_flow = Mock()
    start_flow.authorization_url.return_value = (
        GOOGLE_AUTHORIZATION_URL,
        OAUTH_STATE,
    )
    flow_factory = Mock(return_value=start_flow)
    app, _ = make_app(tmp_path, flow_factory)

    response = asyncio.run(
        get(app, "/auth/google", auth=basic_auth())
    )

    assert response.status_code == 302
    assert response.headers["location"] == GOOGLE_AUTHORIZATION_URL
    start_flow.authorization_url.assert_called_once_with(
        access_type="offline",
        prompt="consent",
    )
    assert start_flow.redirect_uri == REDIRECT_URI


def test_oauth_start_sets_signed_secure_state_cookie(
    tmp_path: Path,
) -> None:
    start_flow = Mock()
    start_flow.authorization_url.return_value = (
        GOOGLE_AUTHORIZATION_URL,
        OAUTH_STATE,
    )
    app, _ = make_app(tmp_path, Mock(return_value=start_flow))

    response = asyncio.run(
        get(app, "/auth/google", auth=basic_auth())
    )

    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("fitbit_oauth_state=")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie


def test_oauth_start_rejects_non_web_client_configuration(
    tmp_path: Path,
) -> None:
    flow_factory = Mock()
    app, _ = make_app(tmp_path, flow_factory)
    (tmp_path / "client_secret_web.json").write_text(
        json.dumps({"installed": {"client_id": "desktop-client-id"}}),
        encoding="utf-8",
    )

    response = asyncio.run(get(app, "/auth/google", auth=basic_auth()))

    assert response.status_code == 500
    assert "desktop-client-id" not in response.text
    flow_factory.assert_not_called()


def test_oauth_callback_rejects_mismatched_state_without_fetching_token(
    tmp_path: Path,
) -> None:
    start_flow = Mock()
    start_flow.authorization_url.return_value = (
        GOOGLE_AUTHORIZATION_URL,
        OAUTH_STATE,
    )
    flow_factory = Mock(return_value=start_flow)
    app, token_path = make_app(tmp_path, flow_factory)

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://fitbit-health.example",
        ) as client:
            await client.get("/auth/google", auth=basic_auth())
            return await client.get(
                "/oauth2/callback?state=wrong-state&code=secret-code"
            )

    response = asyncio.run(exercise())

    assert response.status_code == 400
    assert "secret-code" not in response.text
    assert flow_factory.call_count == 1
    assert not token_path.exists()


def test_oauth_callback_fetches_and_writes_authorized_user_token(
    tmp_path: Path,
    caplog,
) -> None:
    start_flow = Mock()
    start_flow.authorization_url.return_value = (
        GOOGLE_AUTHORIZATION_URL,
        OAUTH_STATE,
    )
    callback_flow = Mock()
    serialized_token = json.dumps({
        "token": "phase-3b-access-token",
        "refresh_token": "phase-3b-refresh-token",
        "client_secret": "phase-3b-client-secret",
    })
    callback_flow.credentials.to_json.return_value = serialized_token
    flow_factory = Mock(side_effect=[start_flow, callback_flow])
    app, token_path = make_app(tmp_path, flow_factory)

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://fitbit-health.example",
        ) as client:
            await client.get("/auth/google", auth=basic_auth())
            return await client.get(
                f"/oauth2/callback?state={OAUTH_STATE}&code=authorization-code"
            )

    response = asyncio.run(exercise())

    assert response.status_code == 200
    assert json.loads(token_path.read_text(encoding="utf-8")) == json.loads(
        serialized_token
    )
    callback_flow.fetch_token.assert_called_once()
    authorization_response = callback_flow.fetch_token.call_args.kwargs[
        "authorization_response"
    ]
    assert "code=authorization-code" in authorization_response
    assert "state=" + OAUTH_STATE in authorization_response
    exposed = response.text + caplog.text
    assert "phase-3b-access-token" not in exposed
    assert "phase-3b-refresh-token" not in exposed
    assert "phase-3b-client-secret" not in exposed
    assert BOOTSTRAP_PASSWORD not in exposed
    assert COOKIE_SECRET not in exposed


def test_oauth_callback_without_refresh_token_preserves_existing_token(
    tmp_path: Path,
) -> None:
    start_flow = Mock()
    start_flow.authorization_url.return_value = (
        GOOGLE_AUTHORIZATION_URL,
        OAUTH_STATE,
    )
    callback_flow = Mock()
    callback_flow.credentials.refresh_token = None
    callback_flow.credentials.to_json.return_value = json.dumps({
        "token": "short-lived-token"
    })
    app, token_path = make_app(
        tmp_path,
        Mock(side_effect=[start_flow, callback_flow]),
    )
    token_path.parent.mkdir(parents=True)
    token_path.write_text('{"token": "existing-token"}', encoding="utf-8")

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://fitbit-health.example",
        ) as client:
            await client.get("/auth/google", auth=basic_auth())
            return await client.get(
                f"/oauth2/callback?state={OAUTH_STATE}&code=authorization-code"
            )

    response = asyncio.run(exercise())

    assert response.status_code == 400
    assert token_path.read_text(encoding="utf-8") == '{"token": "existing-token"}'


def test_oauth_callback_logs_sanitized_exception_without_exposing_credentials(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level("INFO", logger="fitbit_health.web_oauth")
    start_flow = Mock()
    start_flow.authorization_url.return_value = (
        GOOGLE_AUTHORIZATION_URL,
        OAUTH_STATE,
    )
    callback_flow = Mock()
    callback_flow.client_config = {
        "client_id": "web-client-id",
        "client_secret": "configured-client-secret",
    }
    callback_flow.fetch_token.side_effect = RuntimeError(
        "invalid_grant authorization_code=secret-auth-code "
        "access_token=secret-access-token "
        "refresh_token=secret-refresh-token "
        "client_secret=secret-client-secret "
        "credentials=secret-credential-content"
    )
    app, token_path = make_app(
        tmp_path,
        Mock(side_effect=[start_flow, callback_flow]),
    )

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://fitbit-health.example",
        ) as client:
            await client.get("/auth/google", auth=basic_auth())
            return await client.get(
                f"/oauth2/callback?state={OAUTH_STATE}&code=authorization-code"
            )

    response = asyncio.run(exercise())

    assert response.status_code == 400
    assert response.text == "Google authorization failed."
    assert "client_id=web-client-id" in caplog.text
    assert f"redirect_uri={REDIRECT_URI}" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "invalid_grant" in caplog.text
    for sensitive_text in (
        "authorization_code",
        "secret-auth-code",
        "access_token",
        "secret-access-token",
        "refresh_token",
        "secret-refresh-token",
        "client_secret",
        "secret-client-secret",
        "configured-client-secret",
        "credentials",
        "secret-credential-content",
    ):
        assert sensitive_text not in caplog.text
    assert not token_path.exists()


def test_oauth_callback_handles_google_error_without_leaking_details(
    tmp_path: Path,
    caplog,
) -> None:
    start_flow = Mock()
    start_flow.authorization_url.return_value = (
        GOOGLE_AUTHORIZATION_URL,
        OAUTH_STATE,
    )
    flow_factory = Mock(return_value=start_flow)
    app, token_path = make_app(tmp_path, flow_factory)
    secret_detail = "phase-3b-secret-error-detail"

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://fitbit-health.example",
        ) as client:
            await client.get("/auth/google", auth=basic_auth())
            return await client.get(
                "/oauth2/callback"
                f"?state={OAUTH_STATE}&error=access_denied"
                f"&error_description={secret_detail}"
            )

    response = asyncio.run(exercise())

    assert response.status_code == 400
    assert secret_detail not in response.text
    assert secret_detail not in caplog.text
    assert flow_factory.call_count == 1
    assert not token_path.exists()
