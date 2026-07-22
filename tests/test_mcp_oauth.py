import asyncio
import base64
from hashlib import sha256
from importlib import import_module
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.applications import Starlette


ISSUER_URL = "https://fitbit-health-mcp.onrender.com"
RESOURCE_URL = f"{ISSUER_URL}/mcp"
SCOPES = ("health:read",)
CLIENT_ID = "chatgpt-public-client"
REDIRECT_URI = "https://chatgpt.com/connector/oauth/test-callback"
OWNER_PASSWORD = "owner-password"
CODE_CHALLENGE = "A" * 43
CODE_VERIFIER = "v" * 43


def metadata_adapter_type():
    try:
        module = import_module("fitbit_health.mcp_oauth")
    except ModuleNotFoundError:
        pytest.fail("fitbit_health.mcp_oauth metadata adapter is not implemented")

    adapter_type = getattr(module, "MCPOAuthMetadata", None)
    assert adapter_type is not None, "MCPOAuthMetadata is not implemented"
    return adapter_type


def oauth_type(name: str):
    module = import_module("fitbit_health.mcp_oauth")
    implementation_type = getattr(module, name, None)
    assert implementation_type is not None, f"{name} is not implemented"
    return implementation_type


def make_app() -> Starlette:
    adapter = metadata_adapter_type()(
        issuer_url=ISSUER_URL,
        resource_url=RESOURCE_URL,
        scopes=SCOPES,
    )
    return Starlette(routes=adapter.routes())


async def request(
    app: Starlette,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=ISSUER_URL,
    ) as client:
        return await client.request(method, path, **kwargs)


def owner_basic_header(password: str = OWNER_PASSWORD) -> dict[str, str]:
    encoded = base64.b64encode(f"owner:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def make_authorization_app():
    registry = oauth_type("FixedPublicClientRegistry")(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scopes=SCOPES,
    )
    store = oauth_type("AuthorizationCodeStore")()
    token_store = getattr(
        import_module("fitbit_health.mcp_token_store"),
        "OpaqueTokenStore",
    )()
    provider = oauth_type("SingleUserAuthorizationProvider")(
        registry=registry,
        code_store=store,
        token_store=token_store,
        resource_url=RESOURCE_URL,
    )
    authorization = oauth_type("MCPOAuthAuthorization")(
        provider=provider,
        owner_password=OWNER_PASSWORD,
    )
    return Starlette(routes=authorization.routes()), store


def authorization_params(**overrides) -> dict[str, str]:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "health:read",
        "state": "connector-state",
        "code_challenge": CODE_CHALLENGE,
        "code_challenge_method": "S256",
        "resource": RESOURCE_URL,
    }
    params.update(overrides)
    return params


def s256_challenge(verifier: str) -> str:
    digest = sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_token_app():
    registry = oauth_type("FixedPublicClientRegistry")(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scopes=SCOPES,
    )
    code_store = oauth_type("AuthorizationCodeStore")()
    token_store_type = getattr(
        import_module("fitbit_health.mcp_token_store"),
        "OpaqueTokenStore",
    )
    access_tokens = iter(
        ("issued-access-token", "rotated-access-token", "next-access-token")
    )
    refresh_tokens = iter(
        ("issued-refresh-token", "rotated-refresh-token", "next-refresh-token")
    )
    token_store = token_store_type(
        access_token_factory=lambda: next(access_tokens),
        refresh_token_factory=lambda: next(refresh_tokens),
    )
    provider = oauth_type("SingleUserAuthorizationProvider")(
        registry=registry,
        code_store=code_store,
        token_store=token_store,
        resource_url=RESOURCE_URL,
    )
    authorization = oauth_type("MCPOAuthAuthorization")(
        provider=provider,
        owner_password=OWNER_PASSWORD,
    )
    token_endpoint = oauth_type("MCPOAuthTokenEndpoint")(provider=provider)
    app = Starlette(routes=authorization.routes() + token_endpoint.routes())
    return app, code_store, token_store


async def issue_authorization_code(app: Starlette) -> str:
    authorize = await request(
        app,
        "GET",
        "/oauth/authorize",
        params=authorization_params(code_challenge=s256_challenge(CODE_VERIFIER)),
        headers=owner_basic_header(),
    )
    assert authorize.status_code == 302
    return parse_qs(urlsplit(authorize.headers["location"]).query)["code"][0]


def test_protected_resource_metadata_contract() -> None:
    response = asyncio.run(
        request(make_app(), "GET", "/.well-known/oauth-protected-resource")
    )

    assert response.status_code == 200
    assert response.json() == {
        "resource": RESOURCE_URL,
        "authorization_servers": [ISSUER_URL],
        "scopes_supported": ["health:read"],
        "bearer_methods_supported": ["header"],
    }
    assert response.headers["access-control-allow-origin"] == "*"


def test_authorization_server_metadata_contract() -> None:
    response = asyncio.run(
        request(make_app(), "GET", "/.well-known/oauth-authorization-server")
    )

    assert response.status_code == 200
    assert response.json() == {
        "issuer": ISSUER_URL,
        "authorization_endpoint": f"{ISSUER_URL}/oauth/authorize",
        "token_endpoint": f"{ISSUER_URL}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["health:read"],
    }
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    ],
)
def test_metadata_endpoints_support_options(path: str) -> None:
    response = asyncio.run(request(make_app(), "OPTIONS", path))

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["access-control-allow-methods"] == "GET, OPTIONS"


@pytest.mark.parametrize(
    ("issuer_url", "resource_url", "scopes", "message"),
    [
        (
            "http://fitbit-health.example",
            "http://fitbit-health.example/mcp",
            SCOPES,
            "issuer_url",
        ),
        (
            ISSUER_URL,
            f"{ISSUER_URL}/wrong-resource",
            SCOPES,
            "resource_url",
        ),
        (ISSUER_URL, RESOURCE_URL, (), "scopes"),
    ],
)
def test_metadata_adapter_rejects_invalid_configuration(
    issuer_url: str,
    resource_url: str,
    scopes: tuple[str, ...],
    message: str,
) -> None:
    adapter_type = metadata_adapter_type()

    with pytest.raises(ValueError, match=message):
        adapter_type(
            issuer_url=issuer_url,
            resource_url=resource_url,
            scopes=scopes,
        )


def test_fixed_public_client_registry_returns_only_registered_client() -> None:
    registry = oauth_type("FixedPublicClientRegistry")(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scopes=SCOPES,
    )

    async def inspect_registry():
        return (
            await registry.get_client(CLIENT_ID),
            await registry.get_client("unknown-client"),
        )

    known, unknown = asyncio.run(inspect_registry())

    assert known.client_id == CLIENT_ID
    assert [str(uri) for uri in known.redirect_uris] == [REDIRECT_URI]
    assert known.token_endpoint_auth_method == "none"
    assert known.client_secret is None
    assert known.grant_types == ["authorization_code", "refresh_token"]
    assert known.response_types == ["code"]
    assert known.scope == "health:read"
    assert unknown is None


def test_authorization_code_store_hashes_and_consumes_code_once() -> None:
    grant_type = oauth_type("AuthorizationCodeGrant")
    store = oauth_type("AuthorizationCodeStore")(
        ttl_seconds=300,
        clock=lambda: 1_000.0,
        code_factory=lambda: "raw-authorization-code",
    )
    grant = grant_type(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scopes=SCOPES,
        code_challenge="A" * 43,
        resource=RESOURCE_URL,
    )

    async def issue_and_consume():
        code = await store.issue(grant)
        keys_after_issue = tuple(store._codes)
        first = await store.consume(code)
        second = await store.consume(code)
        return code, keys_after_issue, first, second

    code, stored_keys, first, second = asyncio.run(issue_and_consume())

    assert code == "raw-authorization-code"
    assert code not in stored_keys
    assert stored_keys and all(isinstance(key, bytes) for key in stored_keys)
    assert first.client_id == CLIENT_ID
    assert first.redirect_uri == REDIRECT_URI
    assert first.code_challenge == "A" * 43
    assert first.resource == RESOURCE_URL
    assert first.expires_at == 1_300.0
    assert second is None


def test_authorization_code_store_rejects_expired_code() -> None:
    grant_type = oauth_type("AuthorizationCodeGrant")
    now = [1_000.0]
    store = oauth_type("AuthorizationCodeStore")(
        ttl_seconds=300,
        clock=lambda: now[0],
        code_factory=lambda: "expiring-code",
    )
    grant = grant_type(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        scopes=SCOPES,
        code_challenge="B" * 43,
        resource=RESOURCE_URL,
    )

    async def issue_then_expire():
        code = await store.issue(grant)
        now[0] = 1_301.0
        return await store.consume(code)

    assert asyncio.run(issue_then_expire()) is None


def test_authorize_requires_owner_basic_authentication() -> None:
    app, _ = make_authorization_app()

    async def make_requests():
        missing = await request(
            app, "GET", "/oauth/authorize", params=authorization_params()
        )
        wrong = await request(
            app,
            "GET",
            "/oauth/authorize",
            params=authorization_params(),
            headers=owner_basic_header("wrong-password"),
        )
        malformed = await request(
            app,
            "GET",
            "/oauth/authorize",
            params=authorization_params(),
            headers={"Authorization": "Basic !!!"},
        )
        return missing, wrong, malformed

    for response in asyncio.run(make_requests()):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == (
            'Basic realm="Fitbit Health MCP OAuth"'
        )
        assert response.headers["cache-control"] == "no-store"


def test_authorize_issues_one_time_code_with_bound_pkce_and_resource() -> None:
    app, store = make_authorization_app()
    response = asyncio.run(
        request(
            app,
            "GET",
            "/oauth/authorize",
            params=authorization_params(),
            headers=owner_basic_header(),
        )
    )

    assert response.status_code == 302
    redirect = urlsplit(response.headers["location"])
    expected = urlsplit(REDIRECT_URI)
    assert (redirect.scheme, redirect.netloc, redirect.path) == (
        expected.scheme,
        expected.netloc,
        expected.path,
    )
    query = parse_qs(redirect.query)
    assert query["state"] == ["connector-state"]
    code = query["code"][0]

    async def consume_twice():
        return await store.consume(code), await store.consume(code)

    grant, replay = asyncio.run(consume_twice())
    assert grant.client_id == CLIENT_ID
    assert grant.redirect_uri == REDIRECT_URI
    assert grant.scopes == SCOPES
    assert grant.code_challenge == CODE_CHALLENGE
    assert grant.resource == RESOURCE_URL
    assert grant.subject == "single-owner"
    assert replay is None


@pytest.mark.parametrize(
    ("override", "expected_status"),
    [
        ({"client_id": "unknown-client"}, 400),
        ({"redirect_uri": "https://attacker.example/callback"}, 400),
        ({"resource": f"{ISSUER_URL}/other"}, 302),
        ({"code_challenge": "short"}, 302),
        ({"code_challenge_method": "plain"}, 302),
    ],
)
def test_authorize_rejects_unregistered_or_unbound_requests(
    override: dict[str, str],
    expected_status: int,
) -> None:
    app, _ = make_authorization_app()
    response = asyncio.run(
        request(
            app,
            "GET",
            "/oauth/authorize",
            params=authorization_params(**override),
            headers=owner_basic_header(),
        )
    )

    assert response.status_code == expected_status
    if expected_status == 302:
        assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == [
            "invalid_request"
        ]


def test_authorize_route_is_get_only() -> None:
    app, _ = make_authorization_app()
    response = asyncio.run(
        request(
            app,
            "POST",
            "/oauth/authorize",
            params=authorization_params(),
            headers=owner_basic_header(),
        )
    )

    assert response.status_code == 405


def test_token_endpoint_exchanges_code_and_returns_opaque_token_pair() -> None:
    app, _, token_store = make_token_app()

    async def exchange():
        code = await issue_authorization_code(app)
        response = await request(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": CODE_VERIFIER,
            },
        )
        access = await token_store.load_access_token("issued-access-token")
        refresh = await token_store.load_refresh_token("issued-refresh-token")
        return response, access, refresh

    response, access, refresh = asyncio.run(exchange())

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "issued-access-token",
        "token_type": "Bearer",
        "expires_in": 3_600,
        "scope": "health:read",
        "refresh_token": "issued-refresh-token",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert access.resource == refresh.resource == RESOURCE_URL
    assert access.subject == refresh.subject == "single-owner"


def test_pkce_failure_does_not_consume_code_but_success_and_replay_do() -> None:
    app, _, _ = make_token_app()

    async def exercise_code():
        code = await issue_authorization_code(app)
        common = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
        wrong = await request(
            app,
            "POST",
            "/oauth/token",
            data={**common, "code_verifier": "w" * 43},
        )
        correct = await request(
            app,
            "POST",
            "/oauth/token",
            data={**common, "code_verifier": CODE_VERIFIER},
        )
        replay = await request(
            app,
            "POST",
            "/oauth/token",
            data={**common, "code_verifier": CODE_VERIFIER},
        )
        return wrong, correct, replay

    wrong, correct, replay = asyncio.run(exercise_code())

    assert wrong.status_code == 400
    assert wrong.json()["error"] == "invalid_grant"
    assert correct.status_code == 200
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


def test_token_endpoint_rejects_redirect_change_and_non_post_requests() -> None:
    app, _, _ = make_token_app()

    async def make_requests():
        code = await issue_authorization_code(app)
        mismatch = await request(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "redirect_uri": "https://attacker.example/callback",
                "code_verifier": CODE_VERIFIER,
            },
        )
        get_response = await request(app, "GET", "/oauth/token")
        return mismatch, get_response

    mismatch, get_response = asyncio.run(make_requests())

    assert mismatch.status_code == 400
    assert mismatch.json()["error"] == "invalid_request"
    assert get_response.status_code == 405


def test_refresh_exchange_rotates_refresh_token_and_rejects_replay() -> None:
    app, _, token_store = make_token_app()

    async def issue_and_refresh():
        code = await issue_authorization_code(app)
        issued = await request(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": CODE_VERIFIER,
            },
        )
        refresh_token = issued.json()["refresh_token"]
        rotated = await request(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
        replay = await request(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
        old = await token_store.load_refresh_token(refresh_token)
        new = await token_store.load_refresh_token("rotated-refresh-token")
        return rotated, replay, old, new

    rotated, replay, old, new = asyncio.run(issue_and_refresh())

    assert rotated.status_code == 200
    assert rotated.json() == {
        "access_token": "rotated-access-token",
        "token_type": "Bearer",
        "expires_in": 3_600,
        "scope": "health:read",
        "refresh_token": "rotated-refresh-token",
    }
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert old is None
    assert new is not None


def test_refresh_exchange_rejects_scope_expansion_without_rotating() -> None:
    app, _, token_store = make_token_app()

    async def attempt_scope_expansion():
        code = await issue_authorization_code(app)
        issued = await request(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": CODE_VERIFIER,
            },
        )
        refresh_token = issued.json()["refresh_token"]
        response = await request(
            app,
            "POST",
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
                "scope": "health:read admin",
            },
        )
        original = await token_store.load_refresh_token(refresh_token)
        return response, original

    response, original = asyncio.run(attempt_scope_expansion())

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"
    assert original is not None
