from pathlib import Path

import pytest

from fitbit_health import __main__ as cli
from fitbit_health.config import ConfigError


def test_cli_prints_output_paths(monkeypatch, capsys, tmp_path: Path) -> None:
    outputs = tuple(tmp_path / name for name in ("one.json", "two.json", "report.md"))
    monkeypatch.setattr(cli, "run_sync", lambda root, days: outputs)

    assert cli.main(["sync", "--days", "30"]) == 0
    assert capsys.readouterr().out.splitlines() == [str(path) for path in outputs]


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
