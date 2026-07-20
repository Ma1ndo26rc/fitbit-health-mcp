from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Callable
from hmac import compare_digest
import json
import logging
from pathlib import Path
from typing import Any

from google_auth_oauthlib.flow import Flow
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from fitbit_health.config import SCOPES
from fitbit_health.credential_storage import write_authorized_user_token


STATE_SESSION_KEY = "google_oauth_state"
logger = logging.getLogger(__name__)

_SAFE_OAUTH_ERROR_MESSAGES = (
    "access_denied",
    "invalid_grant",
    "mismatching_state",
    "redirect_uri_mismatch",
    "insecure transport",
    "offline google authorization is required",
)


def _sanitize_oauth_error_message(exc: Exception) -> str:
    message = str(exc).lower()
    safe_details = [
        detail for detail in _SAFE_OAUTH_ERROR_MESSAGES if detail in message
    ]
    return ", ".join(safe_details) if safe_details else "[redacted]"


class WebOAuthBootstrap:
    """Single-user Google Web OAuth bootstrap with separate HTTP Basic auth."""

    def __init__(
        self,
        *,
        client_path: Path,
        token_path: Path,
        redirect_uri: str,
        bootstrap_password: str,
        cookie_secret: str,
        flow_factory: Callable[..., Any] = Flow.from_client_secrets_file,
    ) -> None:
        if not bootstrap_password:
            raise ValueError("bootstrap_password must not be empty")
        if not cookie_secret:
            raise ValueError("cookie_secret must not be empty")
        if not redirect_uri:
            raise ValueError("redirect_uri must not be empty")
        self.client_path = client_path
        self.token_path = token_path
        self.redirect_uri = redirect_uri
        self.bootstrap_password = bootstrap_password
        self.cookie_secret = cookie_secret
        self._flow_factory = flow_factory

    def routes(self) -> list[Route]:
        return [
            Route("/auth/google", self.start, methods=["GET"]),
            Route("/oauth2/callback", self.callback, methods=["GET"]),
        ]

    async def start(self, request: Request) -> Response:
        if not self._has_valid_basic_auth(request):
            return PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Fitbit Health OAuth Bootstrap"'
                },
            )

        try:
            flow = self._make_flow()
            authorization_url, state = flow.authorization_url(
                access_type="offline",
                prompt="consent",
            )
        except Exception:
            return PlainTextResponse(
                "Google authorization could not be started.",
                status_code=500,
            )

        request.session[STATE_SESSION_KEY] = state
        return RedirectResponse(authorization_url, status_code=302)

    async def callback(self, request: Request) -> Response:
        expected_state = request.session.pop(STATE_SESSION_KEY, None)
        received_state = request.query_params.get("state")
        if not (
            isinstance(expected_state, str)
            and received_state
            and compare_digest(expected_state, received_state)
        ):
            return PlainTextResponse("Invalid OAuth state.", status_code=400)

        if request.query_params.get("error"):
            return PlainTextResponse("Google authorization failed.", status_code=400)

        try:
            flow = self._make_flow(state=expected_state)
            logger.info(
                "Google OAuth token exchange: client_id=%s redirect_uri=%s",
                flow.client_config.get("client_id", "[unknown]"),
                flow.redirect_uri,
            )
            flow.fetch_token(authorization_response=str(request.url))
            if not flow.credentials.refresh_token:
                raise ValueError("Offline Google authorization is required")
            write_authorized_user_token(
                self.token_path,
                flow.credentials.to_json(),
            )
        except Exception as exc:
            logger.error(
                "Google OAuth callback failed: %s: %s",
                type(exc).__name__,
                _sanitize_oauth_error_message(exc),
            )
            return PlainTextResponse("Google authorization failed.", status_code=400)

        return PlainTextResponse("Google authorization completed.")

    def _make_flow(self, state: str | None = None):
        try:
            client_config = json.loads(self.client_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Google Web OAuth client configuration is unavailable") from exc
        if not (
            isinstance(client_config, dict)
            and isinstance(client_config.get("web"), dict)
        ):
            raise ValueError("Google Web OAuth client configuration is required")

        kwargs: dict[str, Any] = {"scopes": SCOPES}
        if state is not None:
            kwargs["state"] = state
        flow = self._flow_factory(str(self.client_path), **kwargs)
        flow.redirect_uri = self.redirect_uri
        return flow

    def _has_valid_basic_auth(self, request: Request) -> bool:
        authorization = request.headers.get("authorization")
        if authorization is None:
            return False
        scheme, separator, encoded = authorization.partition(" ")
        if not separator or scheme.lower() != "basic" or not encoded:
            return False
        try:
            decoded = b64decode(encoded, validate=True).decode("utf-8")
        except (Base64Error, UnicodeDecodeError, ValueError):
            return False
        username, separator, password = decoded.partition(":")
        if not separator:
            return False
        return compare_digest(username, "bootstrap") and compare_digest(
            password,
            self.bootstrap_password,
        )
