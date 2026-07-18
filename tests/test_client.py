from datetime import date
from unittest.mock import Mock, call

import pytest

from fitbit_health.client import GoogleHealthClient, build_filter


def make_response(status: int, payload: dict | None = None) -> Mock:
    item = Mock(status_code=status)
    item.json.return_value = payload or {}
    return item


def test_fetch_all_follows_next_page_token() -> None:
    session = Mock()
    session.get.side_effect = [
        make_response(200, {"dataPoints": [{"dataPointName": "one"}], "nextPageToken": "next"}),
        make_response(200, {"dataPoints": [{"dataPointName": "two"}], "nextPageToken": ""}),
    ]
    client = GoogleHealthClient(Mock(token="token"), session=session, sleeper=Mock())

    result = client.fetch_all("sleep", date(2026, 7, 1))

    assert [point["dataPointName"] for point in result.data_points] == ["one", "two"]
    assert result.error is None
    assert session.get.call_count == 2
    assert session.get.call_args_list[0].kwargs["params"]["pageSize"] == 25
    assert session.get.call_args_list[1].kwargs["params"]["pageToken"] == "next"


def test_fetch_all_retries_transient_statuses_with_bounded_backoff() -> None:
    session = Mock()
    session.get.side_effect = [make_response(429), make_response(503), make_response(200, {"dataPoints": []})]
    sleeper = Mock()
    client = GoogleHealthClient(Mock(token="token"), session=session, sleeper=sleeper)

    result = client.fetch_all("steps", date(2026, 7, 1))

    assert result.error is None
    assert session.get.call_count == 3
    assert sleeper.call_args_list == [call(1), call(2)]


def test_fetch_all_returns_sanitized_error_for_forbidden() -> None:
    session = Mock()
    forbidden = make_response(403)
    forbidden.text = "secret response body"
    session.get.return_value = forbidden
    client = GoogleHealthClient(Mock(token="token"), session=session, sleeper=Mock())

    result = client.fetch_all("daily-heart-rate-variability", date(2026, 7, 1))

    assert result.data_points == []
    assert result.error == "HTTP 403: permission denied"
    assert "secret" not in result.error


@pytest.mark.parametrize(
    ("data_type", "expected"),
    [
        ("sleep", 'sleep.interval.civil_end_time >= "2026-07-01"'),
        ("steps", 'steps.interval.civil_end_time >= "2026-07-01"'),
        ("heart-rate", 'heart_rate.sample_time.civil_time >= "2026-07-01"'),
        ("daily-resting-heart-rate", 'daily_resting_heart_rate.date >= "2026-07-01"'),
        ("daily-heart-rate-variability", 'daily_heart_rate_variability.date >= "2026-07-01"'),
    ],
)
def test_build_filter_uses_supported_field(data_type: str, expected: str) -> None:
    assert build_filter(data_type, date(2026, 7, 1)) == expected


def test_rejects_unknown_data_type() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        build_filter("unknown", date(2026, 7, 1))
