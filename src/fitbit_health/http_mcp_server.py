import os
from collections.abc import Callable

import uvicorn
from starlette.applications import Starlette

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


def create_http_app(
    service_factory: Callable[[], HealthMCPService] | None = None,
    *,
    token_validator: BearerTokenValidator,
    external_hostname: str | None = None,
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
    app.add_middleware(BearerAuthMiddleware, validator=token_validator)
    return app


def main() -> None:
    """Run the existing Fitbit Health MCP server over Streamable HTTP."""
    bearer_token = os.getenv("MCP_BEARER_TOKEN")
    if not bearer_token:
        raise RuntimeError("MCP_BEARER_TOKEN is required")

    app = create_http_app(
        token_validator=StaticBearerTokenValidator(bearer_token),
        external_hostname=os.getenv("RENDER_EXTERNAL_HOSTNAME"),
    )
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
