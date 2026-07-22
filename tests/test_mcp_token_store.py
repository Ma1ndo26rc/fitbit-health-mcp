import asyncio
from importlib import import_module


CLIENT_ID = "chatgpt-public-client"
RESOURCE_URL = "https://fitbit-health-mcp.onrender.com/mcp"
SCOPES = ("health:read",)


def token_store_type(name: str):
    module = import_module("fitbit_health.mcp_token_store")
    implementation_type = getattr(module, name, None)
    assert implementation_type is not None, f"{name} is not implemented"
    return implementation_type


def test_opaque_token_store_hashes_tokens_and_applies_required_ttls() -> None:
    store = token_store_type("OpaqueTokenStore")(
        clock=lambda: 1_000.0,
        access_token_factory=lambda: "raw-access-token",
        refresh_token_factory=lambda: "raw-refresh-token",
    )

    async def issue_and_load():
        pair = await store.issue(
            client_id=CLIENT_ID,
            scopes=SCOPES,
            resource=RESOURCE_URL,
            subject="single-owner",
        )
        return (
            pair,
            await store.load_access_token(pair.access_token),
            await store.load_refresh_token(pair.refresh_token),
            tuple(store._access_tokens),
            tuple(store._refresh_tokens),
        )

    pair, access, refresh, access_keys, refresh_keys = asyncio.run(issue_and_load())

    assert pair.access_token == "raw-access-token"
    assert pair.refresh_token == "raw-refresh-token"
    assert pair.expires_in == 3_600
    assert access.expires_at == 4_600.0
    assert refresh.expires_at == 2_593_000.0
    assert access.client_id == refresh.client_id == CLIENT_ID
    assert access.scopes == refresh.scopes == SCOPES
    assert access.resource == refresh.resource == RESOURCE_URL
    assert access.subject == refresh.subject == "single-owner"
    assert pair.access_token not in access_keys
    assert pair.refresh_token not in refresh_keys
    assert all(isinstance(key, bytes) for key in access_keys + refresh_keys)
    assert not hasattr(access, "token")
    assert not hasattr(refresh, "token")


def test_opaque_token_store_rejects_expired_access_and_refresh_tokens() -> None:
    now = [1_000.0]
    store = token_store_type("OpaqueTokenStore")(
        clock=lambda: now[0],
        access_token_factory=lambda: "expiring-access",
        refresh_token_factory=lambda: "expiring-refresh",
    )

    async def exercise_expiry():
        pair = await store.issue(
            client_id=CLIENT_ID,
            scopes=SCOPES,
            resource=RESOURCE_URL,
            subject="single-owner",
        )
        now[0] = 4_601.0
        expired_access = await store.load_access_token(pair.access_token)
        valid_refresh = await store.load_refresh_token(pair.refresh_token)
        now[0] = 2_593_001.0
        expired_refresh = await store.load_refresh_token(pair.refresh_token)
        return expired_access, valid_refresh, expired_refresh

    expired_access, valid_refresh, expired_refresh = asyncio.run(exercise_expiry())

    assert expired_access is None
    assert valid_refresh is not None
    assert expired_refresh is None


def test_refresh_rotation_replaces_token_and_rejects_replay() -> None:
    access_tokens = iter(("access-1", "access-2"))
    refresh_tokens = iter(("refresh-1", "refresh-2"))
    store = token_store_type("OpaqueTokenStore")(
        clock=lambda: 1_000.0,
        access_token_factory=lambda: next(access_tokens),
        refresh_token_factory=lambda: next(refresh_tokens),
    )

    async def issue_rotate_and_replay():
        original = await store.issue(
            client_id=CLIENT_ID,
            scopes=SCOPES,
            resource=RESOURCE_URL,
            subject="single-owner",
        )
        rotated = await store.rotate_refresh_token(
            original.refresh_token,
            client_id=CLIENT_ID,
            scopes=SCOPES,
        )
        replay = await store.rotate_refresh_token(
            original.refresh_token,
            client_id=CLIENT_ID,
            scopes=SCOPES,
        )
        old_record = await store.load_refresh_token(original.refresh_token)
        new_record = await store.load_refresh_token(rotated.refresh_token)
        return original, rotated, replay, old_record, new_record

    original, rotated, replay, old_record, new_record = asyncio.run(
        issue_rotate_and_replay()
    )

    assert rotated.access_token == "access-2"
    assert rotated.refresh_token == "refresh-2"
    assert rotated.refresh_token != original.refresh_token
    assert replay is None
    assert old_record is None
    assert new_record.scopes == SCOPES
    assert new_record.resource == RESOURCE_URL


def test_concurrent_refresh_rotation_allows_only_one_success() -> None:
    access_tokens = iter(("access-1", "access-2"))
    refresh_tokens = iter(("refresh-1", "refresh-2"))
    store = token_store_type("OpaqueTokenStore")(
        access_token_factory=lambda: next(access_tokens),
        refresh_token_factory=lambda: next(refresh_tokens),
    )

    async def rotate_concurrently():
        original = await store.issue(
            client_id=CLIENT_ID,
            scopes=SCOPES,
            resource=RESOURCE_URL,
            subject="single-owner",
        )
        return await asyncio.gather(
            store.rotate_refresh_token(
                original.refresh_token,
                client_id=CLIENT_ID,
                scopes=SCOPES,
            ),
            store.rotate_refresh_token(
                original.refresh_token,
                client_id=CLIENT_ID,
                scopes=SCOPES,
            ),
        )

    results = asyncio.run(rotate_concurrently())

    assert sum(result is not None for result in results) == 1
