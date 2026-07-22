import asyncio
import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit

from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from fitbit_health.mcp_token_store import OpaqueTokenStore


_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}


@dataclass(frozen=True)
class AuthorizationCodeGrant:
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    code_challenge: str
    resource: str
    redirect_uri_provided_explicitly: bool = True
    subject: str = "single-owner"
    expires_at: float | None = None


class AuthorizationCodeStore:
    """In-memory, one-time authorization code store keyed only by code digest."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
        code_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._code_factory = code_factory or (lambda: secrets.token_urlsafe(32))
        self._codes: dict[bytes, AuthorizationCodeGrant] = {}
        self._lock = asyncio.Lock()

    async def issue(self, grant: AuthorizationCodeGrant) -> str:
        async with self._lock:
            now = self._clock()
            self._remove_expired(now)
            code = self._code_factory()
            digest = self._digest(code)
            if digest in self._codes:
                raise RuntimeError("authorization code collision")
            self._codes[digest] = replace(
                grant,
                expires_at=now + self._ttl_seconds,
            )
            return code

    async def consume(self, code: str) -> AuthorizationCodeGrant | None:
        digest = self._digest(code)
        async with self._lock:
            grant = self._codes.pop(digest, None)
            if grant is None:
                return None
            if grant.expires_at is None or grant.expires_at <= self._clock():
                return None
            return grant

    async def load(self, code: str) -> AuthorizationCodeGrant | None:
        digest = self._digest(code)
        async with self._lock:
            grant = self._codes.get(digest)
            if grant is None:
                return None
            if grant.expires_at is None or grant.expires_at <= self._clock():
                self._codes.pop(digest, None)
                return None
            return grant

    def _remove_expired(self, now: float) -> None:
        expired = [
            digest
            for digest, grant in self._codes.items()
            if grant.expires_at is None or grant.expires_at <= now
        ]
        for digest in expired:
            self._codes.pop(digest, None)

    @staticmethod
    def _digest(code: str) -> bytes:
        return sha256(code.encode("utf-8")).digest()


class FixedPublicClientRegistry:
    """Registry containing the single pre-registered OAuth public client."""

    def __init__(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        scopes: tuple[str, ...] = ("health:read",),
    ) -> None:
        if not client_id:
            raise ValueError("client_id must not be empty")
        if not redirect_uri:
            raise ValueError("redirect_uri must not be empty")
        if not scopes or any(not scope for scope in scopes):
            raise ValueError("scopes must not be empty")

        self._client = OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[redirect_uri],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(scopes),
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if client_id != self._client.client_id:
            return None
        return self._client


class SingleUserAuthorizationProvider:
    """Authorization provider for the one registered client and MCP resource."""

    def __init__(
        self,
        *,
        registry: FixedPublicClientRegistry,
        code_store: AuthorizationCodeStore,
        token_store: OpaqueTokenStore,
        resource_url: str,
    ) -> None:
        self._registry = registry
        self._code_store = code_store
        self._token_store = token_store
        self._resource_url = resource_url

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self._registry.get_client(client_id)

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        if params.resource != self._resource_url:
            raise AuthorizeError(
                "invalid_request",
                "resource must exactly match the registered MCP resource",
            )
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge) is None:
            raise AuthorizeError(
                "invalid_request",
                "code_challenge must be a valid SHA-256 PKCE challenge",
            )

        redirect_uri = str(params.redirect_uri)
        code = await self._code_store.issue(
            AuthorizationCodeGrant(
                client_id=client.client_id,
                redirect_uri=redirect_uri,
                scopes=tuple(params.scopes or ()),
                code_challenge=params.code_challenge,
                resource=self._resource_url,
                redirect_uri_provided_explicitly=(
                    params.redirect_uri_provided_explicitly
                ),
            )
        )
        return construct_redirect_uri(
            redirect_uri,
            code=code,
            state=params.state,
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        grant = await self._code_store.load(authorization_code)
        if grant is None or grant.client_id != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=list(grant.scopes),
            expires_at=grant.expires_at,
            client_id=grant.client_id,
            code_challenge=grant.code_challenge,
            redirect_uri=grant.redirect_uri,
            redirect_uri_provided_explicitly=(
                grant.redirect_uri_provided_explicitly
            ),
            resource=grant.resource,
            subject=grant.subject,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        grant = await self._code_store.consume(authorization_code.code)
        if grant is None or grant.client_id != client.client_id:
            raise TokenError("invalid_grant", "authorization code was already used")
        pair = await self._token_store.issue(
            client_id=grant.client_id,
            scopes=grant.scopes,
            resource=grant.resource,
            subject=grant.subject,
        )
        return OAuthToken(
            access_token=pair.access_token,
            token_type="Bearer",
            expires_in=pair.expires_in,
            scope=" ".join(grant.scopes),
            refresh_token=pair.refresh_token,
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        record = await self._token_store.load_refresh_token(refresh_token)
        if record is None or record.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record.client_id,
            scopes=list(record.scopes),
            expires_at=int(record.expires_at),
            subject=record.subject,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        pair = await self._token_store.rotate_refresh_token(
            refresh_token.token,
            client_id=client.client_id,
            scopes=tuple(scopes),
        )
        if pair is None:
            raise TokenError("invalid_grant", "refresh token was already used")
        return OAuthToken(
            access_token=pair.access_token,
            token_type="Bearer",
            expires_in=pair.expires_in,
            scope=" ".join(scopes),
            refresh_token=pair.refresh_token,
        )


class MCPOAuthAuthorization:
    """Owner-authenticated adapter for the SDK authorization handler."""

    def __init__(
        self,
        *,
        provider: SingleUserAuthorizationProvider,
        owner_password: str,
    ) -> None:
        if not owner_password:
            raise ValueError("owner_password must not be empty")
        self._owner_password = owner_password
        self._handler = AuthorizationHandler(provider)

    def routes(self) -> list[Route]:
        return [Route("/oauth/authorize", self.authorize, methods=["GET"])]

    async def authorize(self, request: Request) -> Response:
        if not self._has_valid_owner_credentials(request):
            return PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Fitbit Health MCP OAuth"',
                    "Cache-Control": "no-store",
                },
            )
        return await self._handler.handle(request)

    def _has_valid_owner_credentials(self, request: Request) -> bool:
        scheme, separator, credentials = request.headers.get(
            "Authorization", ""
        ).partition(" ")
        if not separator or scheme.lower() != "basic" or not credentials:
            return False
        try:
            decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        username, separator, password = decoded.partition(":")
        if not separator:
            return False
        return hmac.compare_digest(username, "owner") and hmac.compare_digest(
            password,
            self._owner_password,
        )


class MCPOAuthTokenEndpoint:
    """Expose the SDK OAuth token handler for the fixed public client."""

    def __init__(self, *, provider: SingleUserAuthorizationProvider) -> None:
        self._handler = TokenHandler(
            provider=provider,
            client_authenticator=ClientAuthenticator(provider),
        )

    def routes(self) -> list[Route]:
        return [Route("/oauth/token", self.token, methods=["POST"])]

    async def token(self, request: Request) -> Response:
        return await self._handler.handle(request)


class MCPOAuthMetadata:
    """Expose OAuth discovery metadata for the remote MCP resource."""

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        scopes: tuple[str, ...] = ("health:read",),
    ) -> None:
        normalized_issuer = issuer_url.rstrip("/")
        parsed_issuer = urlsplit(normalized_issuer)
        is_local = parsed_issuer.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed_issuer.netloc
            or parsed_issuer.query
            or parsed_issuer.fragment
            or parsed_issuer.path
            or (parsed_issuer.scheme != "https" and not is_local)
        ):
            raise ValueError("issuer_url must be an HTTPS origin")
        if resource_url != f"{normalized_issuer}/mcp":
            raise ValueError("resource_url must be the issuer origin plus /mcp")
        if not scopes or any(not scope for scope in scopes):
            raise ValueError("scopes must not be empty")

        self.issuer_url = normalized_issuer
        self.resource_url = resource_url
        self.scopes = scopes

    def routes(self) -> list[Route]:
        return [
            Route(
                "/.well-known/oauth-protected-resource",
                self.protected_resource,
                methods=["GET", "OPTIONS"],
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                self.authorization_server,
                methods=["GET", "OPTIONS"],
            ),
        ]

    async def protected_resource(self, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_CORS_HEADERS)
        return JSONResponse(
            {
                "resource": self.resource_url,
                "authorization_servers": [self.issuer_url],
                "scopes_supported": list(self.scopes),
                "bearer_methods_supported": ["header"],
            },
            headers=_CORS_HEADERS,
        )

    async def authorization_server(self, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_CORS_HEADERS)
        return JSONResponse(
            {
                "issuer": self.issuer_url,
                "authorization_endpoint": f"{self.issuer_url}/oauth/authorize",
                "token_endpoint": f"{self.issuer_url}/oauth/token",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": list(self.scopes),
            },
            headers=_CORS_HEADERS,
        )
