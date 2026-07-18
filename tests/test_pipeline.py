from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from fitbit_health.client import FetchResult
from fitbit_health.pipeline import DATA_TYPES, PipelineError, run_sync


def test_pipeline_writes_all_outputs_without_real_api(tmp_path: Path) -> None:
    fake_client = Mock()
    fake_client.fetch_all.side_effect = (
        lambda data_type, start: FetchResult(data_type, [])
    )

    paths = run_sync(
        tmp_path,
        days=14,
        today=date(2026, 7, 18),
        client=fake_client,
    )

    assert all(path.exists() for path in paths)
    assert [item.args for item in fake_client.fetch_all.call_args_list] == [
        (data_type, date(2026, 7, 5)) for data_type in DATA_TYPES
    ]


def test_pipeline_allows_partial_failure_and_records_diagnostic(tmp_path: Path) -> None:
    fake_client = Mock()
    fake_client.fetch_all.side_effect = lambda data_type, start: FetchResult(
        data_type,
        [],
        "HTTP 403: permission denied" if data_type == "sleep" else None,
    )

    _, analysis_path, _ = run_sync(
        tmp_path,
        days=7,
        today=date(2026, 7, 18),
        client=fake_client,
    )

    assert "sleep" in analysis_path.read_text(encoding="utf-8")


def test_pipeline_stops_when_every_data_type_fails(tmp_path: Path) -> None:
    fake_client = Mock()
    fake_client.fetch_all.side_effect = (
        lambda data_type, start: FetchResult(data_type, [], "HTTP 500: request failed")
    )

    with pytest.raises(PipelineError, match="全部"):
        run_sync(tmp_path, days=14, today=date(2026, 7, 18), client=fake_client)

    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize("days", [1, 3, 7, 14])
def test_pipeline_accepts_supported_fetch_windows(tmp_path: Path, days: int) -> None:
    fake_client = Mock()
    fake_client.fetch_all.side_effect = lambda data_type, start: FetchResult(data_type, [])

    paths = run_sync(
        tmp_path,
        days=days,
        today=date(2026, 7, 18),
        client=fake_client,
    )

    assert all(path.exists() for path in paths)


@pytest.mark.parametrize("days", [0, 2, 5, 10, 15, 365, True, "7"])
def test_pipeline_rejects_unsupported_fetch_windows(tmp_path: Path, days) -> None:
    with pytest.raises(ValueError, match="14, 7, 3, or 1"):
        run_sync(tmp_path, days=days, client=Mock())
