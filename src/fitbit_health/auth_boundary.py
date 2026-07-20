from hmac import compare_digest
from typing import Protocol

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenValidator(Protocol):
    """Validate an opaque bearer token at the HTTP boundary."""

    async def validate(self, token: str) -> bool:
        """Return whether the supplied bearer token is accepted."""
        ...


class StaticBearerTokenValidator:
    """Temporary validator for the Phase 2B shared-token prototype."""

    def __init__(self, accepted_token: str) -> None:
        if not accepted_token:
            raise ValueError("accepted_token must not be empty")
        self._accepted_token = accepted_token

    async def validate(self, token: str) -> bool:
        return compare_digest(token, self._accepted_token)


class BearerAuthMiddleware:
    """Reject unauthenticated HTTP requests before they reach FastMCP."""

    def __init__(self, app: ASGIApp, validator: BearerTokenValidator) -> None:
        self.app = app
        self.validator = validator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path not in {"/mcp", "/mcp/"}:
            await self.app(scope, receive, send)
            return

        token = self._extract_bearer_token(Headers(scope=scope).get("authorization"))
        authorized = False
        if token is not None:
            try:
                authorized = await self.validator.validate(token)
            except Exception:
                authorized = False

        if not authorized:
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _extract_bearer_token(authorization: str | None) -> str | None:
        if authorization is None:
            return None
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token:
            return None
        return token
