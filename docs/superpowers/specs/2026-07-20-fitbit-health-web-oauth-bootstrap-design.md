# Fitbit Health Web OAuth Bootstrap Design

## Goal

Allow the single-user Render Web Service to start and complete Google Web OAuth in a browser, persist an authorized-user `token.json` at `FITBIT_HEALTH_TOKEN_PATH`, and let the existing MCP runtime consume and refresh that token.

## Scope

- Add `GET /auth/google` protected by independent HTTP Basic authentication.
- Add `GET /oauth2/callback` protected by a signed OAuth state cookie rather than MCP bearer authentication.
- Keep `POST /mcp` protected by the existing MCP bearer validator.
- Use `google_auth_oauthlib.flow.Flow`, `access_type="offline"`, `prompt="consent"`, and `fetch_token(authorization_response=...)`.
- Keep one user, one grant, one token file, and one service instance.

Not included: multi-user storage, a database, Cloudflare, an OAuth provider for MCP callers, ChatGPT integration, Google Health client changes, tool changes, or analytics changes.

## HTTP Boundary

The existing `BearerAuthMiddleware` will enforce authentication only for `/mcp` and `/mcp/`. Other paths pass to Starlette routing.

`GET /auth/google` requires HTTP Basic credentials with the fixed username `bootstrap` and a password supplied through `OAUTH_BOOTSTRAP_PASSWORD`. Invalid or missing credentials return `401` with a Basic challenge. The secret never appears in a URL.

`GET /oauth2/callback` does not accept or require MCP bearer or bootstrap Basic credentials. It accepts only a callback whose `state` matches the signed session state created by `/auth/google`. State is consumed once.

## OAuth Flow

The service reads a Google Web application client JSON from `FITBIT_HEALTH_CLIENT_SECRET_PATH`. The configured callback URI comes from `GOOGLE_OAUTH_REDIRECT_URI` and must exactly match the Google Cloud authorized redirect URI.

The start handler:

1. validates HTTP Basic credentials using constant-time comparison;
2. constructs `Flow.from_client_secrets_file(..., scopes=SCOPES)`;
3. sets `flow.redirect_uri`;
4. calls `authorization_url(access_type="offline", prompt="consent")`;
5. stores the returned state in a signed, HttpOnly, Secure, SameSite=Lax cookie;
6. returns a `302` redirect to Google.

The callback handler:

1. consumes the expected state from the signed cookie;
2. rejects missing or mismatched state with a generic `400` response;
3. rejects Google error callbacks without exposing details;
4. reconstructs `Flow` with the expected state and callback URI;
5. calls `fetch_token(authorization_response=str(request.url))`;
6. writes `flow.credentials.to_json()` to the resolved token path;
7. returns a credential-free success message.

## Token Storage

OAuth writes to the existing `resolve_token_path()` result. A small credential-storage helper creates the parent directory, preserves the existing pre/post `ensure_private_file()` behavior, and writes the authorized-user JSON. Existing MCP refresh and write-back logic remains unchanged.

## Configuration

- `MCP_BEARER_TOKEN`: existing `/mcp` bearer secret.
- `OAUTH_BOOTSTRAP_PASSWORD`: independent browser bootstrap password.
- `OAUTH_COOKIE_SECRET`: signing key for the short-lived OAuth state session cookie.
- `GOOGLE_OAUTH_REDIRECT_URI`: exact public HTTPS callback URI.
- `FITBIT_HEALTH_CLIENT_SECRET_PATH`: Google Web application OAuth client JSON.
- `FITBIT_HEALTH_TOKEN_PATH`: existing mutable authorized-user token path.

Startup fails closed when required OAuth bootstrap settings are missing.

## Error and Leakage Policy

Responses contain only generic authentication, state, OAuth, or persistence errors. Exceptions, authorization codes, access tokens, refresh tokens, client secrets, cookie signing secrets, Basic passwords, and Authorization headers are never returned or logged.

## Tests

- unauthenticated `/auth/google` returns a Basic `401`;
- valid Basic credentials produce a Google redirect with offline access;
- OAuth start creates a Secure, HttpOnly, SameSite=Lax signed state cookie;
- missing or mismatched callback state is rejected and does not fetch a token;
- a valid callback fetches and writes authorized-user JSON to a temporary configured token path;
- callback failures and responses do not leak test credentials;
- `/mcp` remains bearer protected and its tool contract is unchanged;
- full regression suite passes.

