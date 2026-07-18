from pathlib import Path
from unittest.mock import Mock

import pytest

from fitbit_health.auth import AuthError, ensure_private_file, resolve_credentials


def test_reuses_valid_credentials_without_browser() -> None:
    credentials = Mock(valid=True, expired=False)
    flow_factory = Mock()

    assert resolve_credentials(credentials, flow_factory, Mock()) is credentials
    flow_factory.assert_not_called()


def test_refreshes_expired_credentials_with_refresh_token() -> None:
    credentials = Mock(valid=False, expired=True, refresh_token="refresh")
    request = Mock()

    resolved = resolve_credentials(credentials, Mock(), request)

    credentials.refresh.assert_called_once_with(request)
    assert resolved is credentials


def test_starts_manual_loopback_flow_when_credentials_are_absent() -> None:
    granted = Mock(valid=True)
    flow = Mock()
    flow.run_local_server.return_value = granted
    flow_factory = Mock(return_value=flow)

    assert resolve_credentials(None, flow_factory, Mock()) is granted
    flow.run_local_server.assert_called_once_with(
        host="localhost",
        port=0,
        open_browser=False,
        prompt="consent",
        authorization_prompt_message="请在浏览器打开以下 Google 授权链接：\n{url}",
        timeout_seconds=300,
    )


def test_wraps_refresh_failure_without_exposing_token() -> None:
    credentials = Mock(valid=False, expired=True, refresh_token="secret-refresh-token")
    credentials.refresh.side_effect = RuntimeError("secret-refresh-token")

    with pytest.raises(AuthError, match="重新授权") as error:
        resolve_credentials(credentials, Mock(), Mock())

    assert "secret-refresh-token" not in str(error.value)


def test_private_token_remains_writable_for_future_refreshes(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("first", encoding="utf-8")

    ensure_private_file(token_path)
    token_path.write_text("second", encoding="utf-8")

    assert token_path.read_text(encoding="utf-8") == "second"
