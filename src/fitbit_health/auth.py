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
    """Apply best-effort owner-only permissions and hide the token on Windows."""
    try:
        path.chmod(0o600)
    except OSError:
        pass

    if os.name != "nt" or not path.exists():
        return

    file_attribute_hidden = 0x2
    invalid_file_attributes = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    attributes = kernel32.GetFileAttributesW(str(path))
    if attributes != invalid_file_attributes:
        kernel32.SetFileAttributesW(str(path), attributes | file_attribute_hidden)


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
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    ensure_private_file(token_path)
    return credentials
