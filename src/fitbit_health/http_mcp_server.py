import os
from collections.abc import Callable
from pathlib import Path

import uvicorn
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware

from fitbit_health.credential_storage import (
    create_health_service_factory,
    resolve_token_path,
)
from fitbit_health.mcp_server import create_server
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
from fitbit_health.mcp_tools import HealthMCPService
from fitbit_health.mcp_token_store import OpaqueTokenStore
from fitbit_health.web_oauth import WebOAuthBootstrap


def create_http_app(
    service_factory: Callable[[], HealthMCPService] | None = None,
    *,
    token_verifier: TokenVerifier,
    auth_settings: AuthSettings,
    external_hostname: str | None = None,
    mcp_oauth_metadata: MCPOAuthMetadata | None = None,
    mcp_oauth_authorization: MCPOAuthAuthorization | None = None,
    mcp_oauth_token_endpoint: MCPOAuthTokenEndpoint | None = None,
    oauth_bootstrap: WebOAuthBootstrap | None = None,
) -> Starlette:
    """Create an authenticated Streamable HTTP app from the existing server."""
    if service_factory is None:
        service_factory = create_health_service_factory(resolve_token_path())
    server = create_server(
        service_factory=service_factory,
        token_verifier=token_verifier,
        auth_settings=auth_settings,
    )
    if external_hostname is not None:
        allowed_hosts = server.settings.transport_security.allowed_hosts
        if external_hostname not in allowed_hosts:
            allowed_hosts.append(external_hostname)

    app = server.streamable_http_app()
    if mcp_oauth_metadata is not None:
        app.router.routes.extend(mcp_oauth_metadata.routes())
    if mcp_oauth_authorization is not None:
        app.router.routes.extend(mcp_oauth_authorization.routes())
    if mcp_oauth_token_endpoint is not None:
        app.router.routes.extend(mcp_oauth_token_endpoint.routes())
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
    return app


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    """Run the existing Fitbit Health MCP server over Streamable HTTP."""
    bearer_token = _required_environment("MCP_BEARER_TOKEN")
    mcp_oauth_metadata = MCPOAuthMetadata(
        issuer_url=_required_environment("MCP_OAUTH_ISSUER_URL"),
        resource_url=_required_environment("MCP_OAUTH_RESOURCE_URL"),
    )
    mcp_oauth_registry = FixedPublicClientRegistry(
        client_id=_required_environment("MCP_OAUTH_CLIENT_ID"),
        redirect_uri=_required_environment("MCP_OAUTH_REDIRECT_URI"),
        scopes=mcp_oauth_metadata.scopes,
    )
    mcp_oauth_code_store = AuthorizationCodeStore()
    mcp_oauth_token_store = OpaqueTokenStore()
    mcp_auth_settings = AuthSettings(
        issuer_url=mcp_oauth_metadata.issuer_url,
        resource_server_url=mcp_oauth_metadata.resource_url,
        required_scopes=list(mcp_oauth_metadata.scopes),
    )
    mcp_token_verifier = CompositeTokenVerifier(
        verifiers=(
            LegacyStaticTokenVerifier(
                accepted_token=bearer_token,
                resource_url=mcp_oauth_metadata.resource_url,
                scopes=mcp_oauth_metadata.scopes,
            ),
            OpaqueAccessTokenVerifier(
                token_store=mcp_oauth_token_store,
                resource_url=mcp_oauth_metadata.resource_url,
            ),
        )
    )
    mcp_oauth_provider = SingleUserAuthorizationProvider(
        registry=mcp_oauth_registry,
        code_store=mcp_oauth_code_store,
        token_store=mcp_oauth_token_store,
        resource_url=mcp_oauth_metadata.resource_url,
    )
    mcp_oauth_authorization = MCPOAuthAuthorization(
        provider=mcp_oauth_provider,
        owner_password=_required_environment("MCP_OAUTH_OWNER_PASSWORD"),
    )
    mcp_oauth_token_endpoint = MCPOAuthTokenEndpoint(
        provider=mcp_oauth_provider,
    )
    oauth_bootstrap = WebOAuthBootstrap(
        client_path=Path(_required_environment("FITBIT_HEALTH_CLIENT_SECRET_PATH")),
        token_path=resolve_token_path(),
        redirect_uri=_required_environment("GOOGLE_OAUTH_REDIRECT_URI"),
        bootstrap_password=_required_environment("OAUTH_BOOTSTRAP_PASSWORD"),
        cookie_secret=_required_environment("OAUTH_COOKIE_SECRET"),
    )

    app = create_http_app(
        token_verifier=mcp_token_verifier,
        auth_settings=mcp_auth_settings,
        external_hostname=os.getenv("RENDER_EXTERNAL_HOSTNAME"),
        mcp_oauth_metadata=mcp_oauth_metadata,
        mcp_oauth_authorization=mcp_oauth_authorization,
        mcp_oauth_token_endpoint=mcp_oauth_token_endpoint,
        oauth_bootstrap=oauth_bootstrap,
    )
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
