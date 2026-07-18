import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from fitbit_health.auth import (
    AuthError,
    ensure_private_file,
    load_saved_credentials,
    resolve_credentials,
)


SCOPES = ("scope-a", "scope-b")


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


def test_load_saved_credentials_returns_valid_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    credentials = Mock(valid=True, expired=False)
    loader = Mock(return_value=credentials)
    monkeypatch.setattr(
        "fitbit_health.auth.Credentials.from_authorized_user_file", loader
    )

    assert load_saved_credentials(token_path, SCOPES) is credentials
    loader.assert_called_once_with(str(token_path), SCOPES)


def test_load_saved_credentials_refreshes_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    credentials = Mock(
        valid=False,
        expired=True,
        refresh_token="refresh-token",
    )
    credentials.to_json.return_value = json.dumps({"token": "new-token"})
    monkeypatch.setattr(
        "fitbit_health.auth.Credentials.from_authorized_user_file",
        Mock(return_value=credentials),
    )
    request = Mock()

    assert load_saved_credentials(token_path, SCOPES, request=request) is credentials
    credentials.refresh.assert_called_once_with(request)
    assert json.loads(token_path.read_text(encoding="utf-8")) == {
        "token": "new-token"
    }


def test_load_saved_credentials_missing_token_is_noninteractive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    flow_factory = Mock(side_effect=AssertionError("interactive flow must not run"))
    monkeypatch.setattr(
        "fitbit_health.auth.InstalledAppFlow.from_client_secrets_file",
        flow_factory,
    )

    with pytest.raises(AuthError, match="python -m fitbit_health sync --days 1"):
        load_saved_credentials(tmp_path / "missing-token.json", SCOPES)

    flow_factory.assert_not_called()
    assert capsys.readouterr().out == ""


def test_load_saved_credentials_rejects_invalid_token_without_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(
        "fitbit_health.auth.Credentials.from_authorized_user_file",
        Mock(side_effect=ValueError("secret-refresh-token")),
    )

    with pytest.raises(AuthError) as error:
        load_saved_credentials(token_path, SCOPES)

    assert "重新授权" in str(error.value)
    assert "secret-refresh-token" not in str(error.value)


def test_load_saved_credentials_requires_refresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    credentials = Mock(valid=False, expired=True, refresh_token=None)
    monkeypatch.setattr(
        "fitbit_health.auth.Credentials.from_authorized_user_file",
        Mock(return_value=credentials),
    )

    with pytest.raises(AuthError, match="python -m fitbit_health sync --days 1"):
        load_saved_credentials(token_path, SCOPES)

    credentials.refresh.assert_not_called()


def test_load_saved_credentials_sanitizes_refresh_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text("{}", encoding="utf-8")
    credentials = Mock(
        valid=False,
        expired=True,
        refresh_token="secret-refresh-token",
    )
    credentials.refresh.side_effect = RuntimeError("secret-refresh-token")
    monkeypatch.setattr(
        "fitbit_health.auth.Credentials.from_authorized_user_file",
        Mock(return_value=credentials),
    )

    with pytest.raises(AuthError) as error:
        load_saved_credentials(token_path, SCOPES, request=Mock())

    assert "重新授权" in str(error.value)
    assert "secret-refresh-token" not in str(error.value)
