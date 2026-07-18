import json
from pathlib import Path

import pytest

from fitbit_health.config import ConfigError, find_installed_credentials


def write_client(path: Path, kind: str) -> None:
    path.write_text(
        json.dumps({kind: {"client_id": "test", "client_secret": "secret"}}),
        encoding="utf-8",
    )


def test_prefers_installed_client_over_web_client(tmp_path: Path) -> None:
    write_client(tmp_path / "client_secret_web.json", "web")
    installed = tmp_path / "client_secret_desktop.json"
    write_client(installed, "installed")

    assert find_installed_credentials(tmp_path) == installed


def test_rejects_missing_installed_client(tmp_path: Path) -> None:
    write_client(tmp_path / "client_secret_web.json", "web")

    with pytest.raises(ConfigError, match="桌面设备"):
        find_installed_credentials(tmp_path)


def test_rejects_multiple_installed_clients(tmp_path: Path) -> None:
    write_client(tmp_path / "client_secret_desktop_a.json", "installed")
    write_client(tmp_path / "client_secret_desktop_b.json", "installed")

    with pytest.raises(ConfigError, match="多个"):
        find_installed_credentials(tmp_path)
