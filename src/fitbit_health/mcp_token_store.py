import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import secrets
import time


ACCESS_TOKEN_TTL_SECONDS = 3_600
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class AccessTokenRecord:
    client_id: str
    scopes: tuple[str, ...]
    resource: str
    subject: str
    expires_at: float


@dataclass(frozen=True)
class RefreshTokenRecord:
    client_id: str
    scopes: tuple[str, ...]
    resource: str
    subject: str
    expires_at: float


@dataclass(frozen=True)
class IssuedTokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class OpaqueTokenStore:
    """Store opaque OAuth tokens by SHA-256 digest, never by raw value."""

    def __init__(
        self,
        *,
        access_token_ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
        refresh_token_ttl_seconds: int = REFRESH_TOKEN_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        access_token_factory: Callable[[], str] | None = None,
        refresh_token_factory: Callable[[], str] | None = None,
    ) -> None:
        if access_token_ttl_seconds <= 0:
            raise ValueError("access_token_ttl_seconds must be positive")
        if refresh_token_ttl_seconds <= 0:
            raise ValueError("refresh_token_ttl_seconds must be positive")
        self._access_token_ttl_seconds = access_token_ttl_seconds
        self._refresh_token_ttl_seconds = refresh_token_ttl_seconds
        self._clock = clock
        self._access_token_factory = access_token_factory or (
            lambda: secrets.token_urlsafe(32)
        )
        self._refresh_token_factory = refresh_token_factory or (
            lambda: secrets.token_urlsafe(48)
        )
        self._access_tokens: dict[bytes, AccessTokenRecord] = {}
        self._refresh_tokens: dict[bytes, RefreshTokenRecord] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self,
        *,
        client_id: str,
        scopes: tuple[str, ...],
        resource: str,
        subject: str,
    ) -> IssuedTokenPair:
        async with self._lock:
            now = self._clock()
            self._remove_expired(now)
            return self._issue_locked(
                client_id=client_id,
                scopes=scopes,
                resource=resource,
                subject=subject,
                now=now,
            )

    async def load_access_token(self, token: str) -> AccessTokenRecord | None:
        async with self._lock:
            record = self._access_tokens.get(self._digest(token))
            if record is None:
                return None
            if record.expires_at <= self._clock():
                self._access_tokens.pop(self._digest(token), None)
                return None
            return record

    async def load_refresh_token(self, token: str) -> RefreshTokenRecord | None:
        async with self._lock:
            record = self._refresh_tokens.get(self._digest(token))
            if record is None:
                return None
            if record.expires_at <= self._clock():
                self._refresh_tokens.pop(self._digest(token), None)
                return None
            return record

    async def rotate_refresh_token(
        self,
        token: str,
        *,
        client_id: str,
        scopes: tuple[str, ...],
    ) -> IssuedTokenPair | None:
        digest = self._digest(token)
        async with self._lock:
            now = self._clock()
            record = self._refresh_tokens.get(digest)
            if record is None or record.expires_at <= now:
                self._refresh_tokens.pop(digest, None)
                return None
            if record.client_id != client_id:
                return None
            if any(scope not in record.scopes for scope in scopes):
                return None

            self._refresh_tokens.pop(digest)
            self._remove_expired(now)
            return self._issue_locked(
                client_id=record.client_id,
                scopes=scopes,
                resource=record.resource,
                subject=record.subject,
                now=now,
            )

    def _issue_locked(
        self,
        *,
        client_id: str,
        scopes: tuple[str, ...],
        resource: str,
        subject: str,
        now: float,
    ) -> IssuedTokenPair:
        access_token = self._access_token_factory()
        refresh_token = self._refresh_token_factory()
        access_digest = self._digest(access_token)
        refresh_digest = self._digest(refresh_token)
        if access_digest in self._access_tokens:
            raise RuntimeError("access token collision")
        if refresh_digest in self._refresh_tokens:
            raise RuntimeError("refresh token collision")

        self._access_tokens[access_digest] = AccessTokenRecord(
            client_id=client_id,
            scopes=scopes,
            resource=resource,
            subject=subject,
            expires_at=now + self._access_token_ttl_seconds,
        )
        self._refresh_tokens[refresh_digest] = RefreshTokenRecord(
            client_id=client_id,
            scopes=scopes,
            resource=resource,
            subject=subject,
            expires_at=now + self._refresh_token_ttl_seconds,
        )
        return IssuedTokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._access_token_ttl_seconds,
        )

    def _remove_expired(self, now: float) -> None:
        self._access_tokens = {
            digest: record
            for digest, record in self._access_tokens.items()
            if record.expires_at > now
        }
        self._refresh_tokens = {
            digest: record
            for digest, record in self._refresh_tokens.items()
            if record.expires_at > now
        }

    @staticmethod
    def _digest(token: str) -> bytes:
        return sha256(token.encode("utf-8")).digest()
