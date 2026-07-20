import os
from collections.abc import Callable
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware

from fitbit_health.auth_boundary import (
    BearerAuthMiddleware,
    BearerTokenValidator,
    StaticBearerTokenValidator,
)
from fitbit_health.credential_storage import (
    create_health_service_factory,
    resolve_token_path,
)
from fitbit_health.mcp_server import create_server
from fitbit_health.mcp_tools import HealthMCPService
from fitbit_health.web_oauth import WebOAuthBootstrap


def create_http_app(
    service_factory: Callable[[], HealthMCPService] | None = None,
    *,
    token_validator: BearerTokenValidator,
    external_hostname: str | None = None,
    oauth_bootstrap: WebOAuthBootstrap | None = None,
) -> Starlette:
    """Create an authenticated Streamable HTTP app from the existing server."""
    if service_factory is None:
        service_factory = create_health_service_factory(resolve_token_path())
    server = create_server(service_factory=service_factory)
    if external_hostname is not None:
        allowed_hosts = server.settings.transport_security.allowed_hosts
        if external_hostname not in allowed_hosts:
            allowed_hosts.append(external_hostname)

    app = server.streamable_http_app()
    if oauth_bootstrap is not None:
        app.router.routes.extend(oauth_bootstrap.routes())
        app.add_middleware(
            SessionMiddleware,
            secret_key=oauth_bootstrap.cookie_secret,
            session_cookie="fitbit_oauth_state",
            max_age=600,
            same_site="lax",
            https_only=True,
        )
    app.add_middleware(BearerAuthMiddleware, validator=token_validator)
    return app


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    """Run the existing Fitbit Health MCP server over Streamable HTTP."""
    bearer_token = _required_environment("MCP_BEARER_TOKEN")
    oauth_bootstrap = WebOAuthBootstrap(
        client_path=Path(_required_environment("FITBIT_HEALTH_CLIENT_SECRET_PATH")),
        token_path=resolve_token_path(),
        redirect_uri=_required_environment("GOOGLE_OAUTH_REDIRECT_URI"),
        bootstrap_password=_required_environment("OAUTH_BOOTSTRAP_PASSWORD"),
        cookie_secret=_required_environment("OAUTH_COOKIE_SECRET"),
    )

    app = create_http_app(
        token_validator=StaticBearerTokenValidator(bearer_token),
        external_hostname=os.getenv("RENDER_EXTERNAL_HOSTNAME"),
        oauth_bootstrap=oauth_bootstrap,
    )
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
