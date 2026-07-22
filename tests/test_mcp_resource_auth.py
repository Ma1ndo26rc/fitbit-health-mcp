import asyncio
from importlib import import_module

from fitbit_health.mcp_token_store import OpaqueTokenStore


LEGACY_TOKEN = "legacy-static-token"
RESOURCE_URL = "https://fitbit-health-mcp.onrender.com/mcp"
SCOPES = ("health:read",)


def resource_auth_type(name: str):
    module = import_module("fitbit_health.mcp_resource_auth")
    implementation_type = getattr(module, name, None)
    assert implementation_type is not None, f"{name} is not implemented"
    return implementation_type


def test_legacy_verifier_accepts_only_static_token_without_storing_plaintext() -> None:
    verifier = resource_auth_type("LegacyStaticTokenVerifier")(
        accepted_token=LEGACY_TOKEN,
        resource_url=RESOURCE_URL,
        scopes=SCOPES,
    )

    async def verify():
        return (
            await verifier.verify_token(LEGACY_TOKEN),
            await verifier.verify_token("wrong-token"),
        )

    accepted, rejected = asyncio.run(verify())

    assert accepted.client_id == "legacy-static-client"
    assert accepted.scopes == ["health:read"]
    assert accepted.resource == RESOURCE_URL
    assert accepted.subject == "single-owner"
    assert accepted.expires_at is None
    assert rejected is None
    assert LEGACY_TOKEN not in repr(vars(verifier))
    assert not hasattr(verifier, "_accepted_token")


def test_opaque_verifier_loads_hash_stored_access_token_for_exact_resource() -> None:
    store = OpaqueTokenStore(
        access_token_factory=lambda: "opaque-access-token",
        refresh_token_factory=lambda: "opaque-refresh-token",
    )
    verifier = resource_auth_type("OpaqueAccessTokenVerifier")(
        token_store=store,
        resource_url=RESOURCE_URL,
    )

    async def issue_and_verify():
        pair = await store.issue(
            client_id="chatgpt-public-client",
            scopes=SCOPES,
            resource=RESOURCE_URL,
            subject="single-owner",
        )
        return await verifier.verify_token(pair.access_token)

    access = asyncio.run(issue_and_verify())

    assert access.token == "opaque-access-token"
    assert access.client_id == "chatgpt-public-client"
    assert access.scopes == ["health:read"]
    assert access.resource == RESOURCE_URL
    assert access.subject == "single-owner"
    assert isinstance(access.expires_at, int)


def test_opaque_verifier_rejects_wrong_resource_and_unknown_token() -> None:
    store = OpaqueTokenStore(
        access_token_factory=lambda: "wrong-resource-access",
        refresh_token_factory=lambda: "wrong-resource-refresh",
    )
    verifier = resource_auth_type("OpaqueAccessTokenVerifier")(
        token_store=store,
        resource_url=RESOURCE_URL,
    )

    async def verify():
        pair = await store.issue(
            client_id="chatgpt-public-client",
            scopes=SCOPES,
            resource="https://fitbit-health-mcp.onrender.com/other",
            subject="single-owner",
        )
        return (
            await verifier.verify_token(pair.access_token),
            await verifier.verify_token("unknown-token"),
        )

    wrong_resource, unknown = asyncio.run(verify())

    assert wrong_resource is None
    assert unknown is None


def test_composite_verifier_accepts_either_token_and_fails_closed() -> None:
    class FailingVerifier:
        async def verify_token(self, token: str):
            raise RuntimeError("verification backend unavailable")

    legacy = resource_auth_type("LegacyStaticTokenVerifier")(
        accepted_token=LEGACY_TOKEN,
        resource_url=RESOURCE_URL,
        scopes=SCOPES,
    )
    composite = resource_auth_type("CompositeTokenVerifier")(
        verifiers=(FailingVerifier(), legacy),
    )

    async def verify():
        return (
            await composite.verify_token(LEGACY_TOKEN),
            await composite.verify_token("invalid-token"),
        )

    accepted, rejected = asyncio.run(verify())

    assert accepted is not None
    assert accepted.client_id == "legacy-static-client"
    assert rejected is None
