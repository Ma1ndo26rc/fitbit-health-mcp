from pathlib import Path
from unittest.mock import Mock

import pytest

from fitbit_health import __main__ as cli
from fitbit_health.config import ConfigError


def test_cli_prints_output_paths(monkeypatch, capsys, tmp_path: Path) -> None:
    outputs = tuple(tmp_path / name for name in ("one.json", "two.json", "report.md"))
    monkeypatch.setattr(cli, "run_sync", lambda root, days: outputs)

    assert cli.main(["sync", "--days", "14"]) == 0
    assert capsys.readouterr().out.splitlines() == [str(path) for path in outputs]


@pytest.mark.parametrize("days", ["14", "7", "3", "1"])
def test_cli_accepts_supported_fetch_windows(monkeypatch, tmp_path: Path, days: str) -> None:
    outputs = tuple(tmp_path / name for name in ("one.json", "two.json", "report.md"))
    run_sync = Mock(return_value=outputs)
    monkeypatch.setattr(cli, "run_sync", run_sync)

    assert cli.main(["sync", "--days", days]) == 0
    run_sync.assert_called_once_with(Path.cwd(), int(days))


def test_cli_defaults_to_seven_days(monkeypatch, tmp_path: Path) -> None:
    outputs = tuple(tmp_path / name for name in ("one.json", "two.json", "report.md"))
    run_sync = Mock(return_value=outputs)
    monkeypatch.setattr(cli, "run_sync", run_sync)

    assert cli.main(["sync"]) == 0
    run_sync.assert_called_once_with(Path.cwd(), 7)


@pytest.mark.parametrize("days", ["2", "5", "10", "15", "365"])
def test_cli_rejects_unsupported_fetch_windows(days: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["sync", "--days", days])

    assert error.value.code == 2


def test_cli_returns_sanitized_configuration_error(monkeypatch, capsys) -> None:
    def fail(root, days):
        raise ConfigError("未找到桌面设备凭据")

    monkeypatch.setattr(cli, "run_sync", fail)

    assert cli.main(["sync"]) == 2
    captured = capsys.readouterr()
    assert "未找到桌面设备凭据" in captured.err


def test_cli_rejects_days_outside_supported_range() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["sync", "--days", "0"])

    assert error.value.code == 2
