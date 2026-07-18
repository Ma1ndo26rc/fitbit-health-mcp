import ctypes
import os
from pathlib import Path
import sys
from typing import Callable, Protocol

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


class AuthError(RuntimeError):
    """Raised when OAuth cannot provide usable credentials."""


MCP_AUTH_MESSAGE = (
    "本地 Google 授权不可用，请在普通终端运行 "
    "python -m fitbit_health sync --days 1 重新授权。"
)


class LocalFlow(Protocol):
    def run_local_server(self, **kwargs): ...


def resolve_credentials(
    credentials: Credentials | None,
    flow_factory: Callable[[], LocalFlow],
    request: Request,
) -> Credentials:
    """Reuse, refresh, or interactively create OAuth credentials."""
    if credentials is not None and credentials.valid:
        return credentials

    if credentials is not None and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(request)
        except Exception as exc:
            raise AuthError("Google token 刷新失败，请重新授权。") from exc
        return credentials

    try:
        flow = flow_factory()
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(line_buffering=True, write_through=True)
        else:
            sys.stdout.flush()
        return flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=False,
            prompt="consent",
            authorization_prompt_message="请在浏览器打开以下 Google 授权链接：\n{url}",
            timeout_seconds=300,
        )
    except Exception as exc:
        raise AuthError("Google 授权失败，请检查测试用户、权限范围和网络连接后重新授权。") from exc


def ensure_private_file(path: Path) -> None:
    """Apply best-effort owner-only permissions without blocking token refresh."""
    if not path.exists():
        return

    if os.name == "nt":
        file_attribute_readonly = 0x1
        file_attribute_hidden = 0x2
        invalid_file_attributes = 0xFFFFFFFF
        kernel32 = ctypes.windll.kernel32
        attributes = kernel32.GetFileAttributesW(str(path))
        if attributes != invalid_file_attributes:
            writable_visible = attributes & ~file_attribute_hidden & ~file_attribute_readonly
            kernel32.SetFileAttributesW(str(path), writable_visible)
        return

    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_credentials(
    client_path: Path,
    token_path: Path,
    scopes: tuple[str, ...],
) -> Credentials:
    """Load a saved token or complete the installed-app OAuth flow."""
    existing: Credentials | None = None
    if token_path.exists():
        try:
            existing = Credentials.from_authorized_user_file(str(token_path), scopes)
        except (OSError, ValueError) as exc:
            raise AuthError("本地 token 文件无效，请移走后重新授权。") from exc

    def make_flow() -> InstalledAppFlow:
        return InstalledAppFlow.from_client_secrets_file(str(client_path), scopes)

    credentials = resolve_credentials(existing, make_flow, Request())
    token_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_private_file(token_path)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    ensure_private_file(token_path)
    return credentials


def load_saved_credentials(
    token_path: Path,
    scopes: tuple[str, ...],
    request: Request | None = None,
) -> Credentials:
    """Load or refresh saved OAuth credentials without user interaction."""
    if not token_path.exists():
        raise AuthError(MCP_AUTH_MESSAGE)

    try:
        credentials = Credentials.from_authorized_user_file(str(token_path), scopes)
    except (OSError, ValueError) as exc:
        raise AuthError(MCP_AUTH_MESSAGE) from exc

    if credentials.valid:
        return credentials

    if not (credentials.expired and credentials.refresh_token):
        raise AuthError(MCP_AUTH_MESSAGE)

    try:
        credentials.refresh(request or Request())
        ensure_private_file(token_path)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        ensure_private_file(token_path)
    except Exception as exc:
        raise AuthError(MCP_AUTH_MESSAGE) from exc
    return credentials
