from hashlib import sha256
from hmac import compare_digest

from mcp.server.auth.provider import AccessToken, TokenVerifier

from fitbit_health.mcp_token_store import OpaqueTokenStore


class LegacyStaticTokenVerifier:
    """Verify the legacy shared token without retaining its plaintext value."""

    def __init__(
        self,
        *,
        accepted_token: str,
        resource_url: str,
        scopes: tuple[str, ...] = ("health:read",),
    ) -> None:
        if not accepted_token:
            raise ValueError("accepted_token must not be empty")
        if not resource_url:
            raise ValueError("resource_url must not be empty")
        if not scopes or any(not scope for scope in scopes):
            raise ValueError("scopes must not be empty")
        self._accepted_digest = self._digest(accepted_token)
        self._resource_url = resource_url
        self._scopes = scopes

    async def verify_token(self, token: str) -> AccessToken | None:
        if not compare_digest(self._digest(token), self._accepted_digest):
            return None
        return AccessToken(
            token=token,
            client_id="legacy-static-client",
            scopes=list(self._scopes),
            resource=self._resource_url,
            subject="single-owner",
        )

    @staticmethod
    def _digest(token: str) -> bytes:
        return sha256(token.encode("utf-8")).digest()


class OpaqueAccessTokenVerifier:
    """Verify access tokens issued by the shared opaque token store."""

    def __init__(
        self,
        *,
        token_store: OpaqueTokenStore,
        resource_url: str,
    ) -> None:
        if not resource_url:
            raise ValueError("resource_url must not be empty")
        self._token_store = token_store
        self._resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        record = await self._token_store.load_access_token(token)
        if record is None or record.resource != self._resource_url:
            return None
        return AccessToken(
            token=token,
            client_id=record.client_id,
            scopes=list(record.scopes),
            expires_at=int(record.expires_at),
            resource=record.resource,
            subject=record.subject,
        )


class CompositeTokenVerifier:
    """Accept a token when any configured verifier accepts it."""

    def __init__(self, *, verifiers: tuple[TokenVerifier, ...]) -> None:
        if not verifiers:
            raise ValueError("verifiers must not be empty")
        self._verifiers = verifiers

    async def verify_token(self, token: str) -> AccessToken | None:
        for verifier in self._verifiers:
            try:
                access_token = await verifier.verify_token(token)
            except Exception:
                continue
            if access_token is not None:
                return access_token
        return None
