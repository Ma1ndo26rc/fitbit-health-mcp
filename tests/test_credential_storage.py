import json
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from fitbit_health.auth import load_saved_credentials
from fitbit_health.credential_storage import (
    CLIENT_SECRET_SOURCE_ENV,
    TOKEN_PATH_ENV,
    create_health_service_factory,
    resolve_token_path,
)


SCOPES = ("scope-a", "scope-b")


def test_token_path_can_be_configured_for_persistent_storage(tmp_path: Path) -> None:
    token_path = tmp_path / "render-disk" / ".private" / "token.json"

    resolved = resolve_token_path(
        environ={TOKEN_PATH_ENV: str(token_path)},
        cwd=tmp_path / "source",
    )
    service = create_health_service_factory(resolved)()

    assert resolved == token_path.resolve()
    assert service.root == token_path.parent.parent.resolve()


def test_refreshed_token_is_reloaded_from_same_path_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token_path = resolve_token_path(
        environ={
            TOKEN_PATH_ENV: str(tmp_path / "runtime" / ".private" / "token.json")
        },
        cwd=tmp_path,
    )
    token_path.parent.mkdir(parents=True)
    token_path.write_text("{}", encoding="utf-8")
    secret_refresh_token = "phase-2c-secret-refresh-token"
    refreshed_json = json.dumps(
        {"token": "new-access-token", "refresh_token": secret_refresh_token}
    )
    expired = Mock(valid=False, expired=True, refresh_token=secret_refresh_token)
    expired.to_json.return_value = refreshed_json
    restarted = Mock(valid=True, expired=False)
    loader = Mock(side_effect=[expired, restarted])
    monkeypatch.setattr(
        "fitbit_health.auth.Credentials.from_authorized_user_file",
        loader,
    )

    load_saved_credentials(token_path, SCOPES, request=Mock())
    reloaded = load_saved_credentials(token_path, SCOPES)

    assert reloaded is restarted
    assert json.loads(token_path.read_text(encoding="utf-8")) == {
        "token": "new-access-token",
        "refresh_token": secret_refresh_token,
    }
    assert loader.call_args_list == [
        call(str(token_path), SCOPES),
        call(str(token_path), SCOPES),
    ]
    captured = capsys.readouterr()
    assert secret_refresh_token not in caplog.text
    assert secret_refresh_token not in captured.out
    assert secret_refresh_token not in captured.err


def test_client_credential_is_staged_without_leaking(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = Path(__file__).parents[1]
    gitignore = (repository_root / ".gitignore").read_text(encoding="utf-8")
    service_root = tmp_path / "runtime"
    token_path = service_root / ".private" / "token.json"
    source = tmp_path / "secrets" / "client_secret_render.json"
    source.parent.mkdir()
    client_secret = "phase-2c-client-secret"
    source.write_text(json.dumps({"installed": {"client_secret": client_secret}}))

    service = create_health_service_factory(
        token_path,
        environ={CLIENT_SECRET_SOURCE_ENV: str(source)},
    )()
    staged = service.root / source.name

    assert "client_secret_*.json" in gitignore
    assert ".private/" in gitignore
    assert staged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert not list((repository_root / "src").rglob("client_secret_*.json"))
    assert not list((repository_root / "src").rglob("token.json"))
    captured = capsys.readouterr()
    assert client_secret not in caplog.text
    assert client_secret not in captured.out
    assert client_secret not in captured.err


def test_token_seed_is_copied_only_when_runtime_token_is_missing(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "runtime" / ".private" / "token.json"
    seed_path = tmp_path / "secrets" / "token.json"
    seed_path.parent.mkdir()
    seed_path.write_text('{"token": "seed"}', encoding="utf-8")

    create_health_service_factory(
        token_path,
        environ={"FITBIT_HEALTH_TOKEN_SEED_PATH": str(seed_path)},
    )()

    assert token_path.read_text(encoding="utf-8") == '{"token": "seed"}'

    token_path.write_text('{"token": "refreshed"}', encoding="utf-8")
    create_health_service_factory(
        token_path,
        environ={"FITBIT_HEALTH_TOKEN_SEED_PATH": str(seed_path)},
    )()

    assert token_path.read_text(encoding="utf-8") == '{"token": "refreshed"}'
