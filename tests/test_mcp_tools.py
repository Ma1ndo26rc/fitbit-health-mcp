import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from fitbit_health.auth import AuthError
from fitbit_health.client import FetchResult
from fitbit_health.mcp_tools import HealthMCPService
from fitbit_health.pipeline import DATA_TYPES


TODAY = date(2026, 7, 18)
START = date(2026, 7, 12)
ENVELOPE_FIELDS = {
    "requested_days",
    "available_days",
    "data",
    "missing_data",
    "diagnostics",
}


def health_results() -> dict[str, FetchResult]:
    proto_date = {"year": 2026, "month": 7, "day": 18}
    return {
        "sleep": FetchResult("sleep", [{
            "sleep": {
                "interval": {
                    "startTime": "2026-07-17T15:00:00Z",
                    "startUtcOffset": "28800s",
                    "endTime": "2026-07-17T22:30:00Z",
                    "endUtcOffset": "28800s",
                },
                "summary": {
                    "minutesAsleep": "420",
                    "minutesAwake": "30",
                    "stagesSummary": [
                        {"type": "DEEP", "minutes": "80"},
                        {"type": "REM", "minutes": "100"},
                        {"type": "LIGHT", "minutes": "240"},
                    ],
                },
            }
        }]),
        "steps": FetchResult("steps", [{
            "steps": {
                "interval": {"civilEndTime": {"date": proto_date}},
                "count": "2000",
            }
        }]),
        "heart-rate": FetchResult("heart-rate", [{
            "heartRate": {
                "sampleTime": {"civilTime": {"date": proto_date}},
                "beatsPerMinute": "65",
            }
        }]),
        "daily-resting-heart-rate": FetchResult(
            "daily-resting-heart-rate",
            [{"dailyRestingHeartRate": {
                "date": proto_date,
                "beatsPerMinute": "55",
            }}],
        ),
        "daily-heart-rate-variability": FetchResult(
            "daily-heart-rate-variability",
            [{"dailyHeartRateVariability": {
                "date": proto_date,
                "averageHeartRateVariabilityMilliseconds": 62.5,
            }}],
        ),
    }


def make_service(
    results: dict[str, FetchResult] | None = None,
    client_factory=None,
) -> tuple[HealthMCPService, Mock | None]:
    if client_factory is not None:
        return (
            HealthMCPService(
                Path("."), client_factory=client_factory, today_factory=lambda: TODAY
            ),
            None,
        )
    fake_client = Mock()
    values = results if results is not None else health_results()
    fake_client.fetch_all.side_effect = lambda data_type, start: values[data_type]
    return (
        HealthMCPService(
            Path("."),
            client_factory=lambda: fake_client,
            today_factory=lambda: TODAY,
        ),
        fake_client,
    )


@pytest.mark.parametrize(
    ("method_name", "data_type", "fields"),
    [
        (
            "get_sleep",
            "sleep",
            {
                "date",
                "minutes_asleep",
                "minutes_awake",
                "deep_minutes",
                "rem_minutes",
                "light_minutes",
                "start_time",
                "end_time",
            },
        ),
        ("get_steps", "steps", {"date", "steps"}),
        (
            "get_heart_rate",
            "heart-rate",
            {"date", "heart_rate_average"},
        ),
        (
            "get_resting_heart_rate",
            "daily-resting-heart-rate",
            {"date", "resting_heart_rate"},
        ),
        (
            "get_hrv",
            "daily-heart-rate-variability",
            {"date", "hrv_rmssd"},
        ),
    ],
)
def test_metric_tools_return_fixed_json_schema_and_fetch_only_their_type(
    method_name: str, data_type: str, fields: set[str]
) -> None:
    service, fake_client = make_service()

    result = getattr(service, method_name)(days=7)

    assert set(result) == ENVELOPE_FIELDS
    assert result["requested_days"] == 7
    assert result["available_days"] == 1
    assert set(result["data"][0]) == fields
    assert result["data"][0]["date"] == "2026-07-18"
    assert result["missing_data"] == [
        "2026-07-12",
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    ]
    assert result["diagnostics"] == {}
    assert fake_client is not None
    fake_client.fetch_all.assert_called_once_with(data_type, START)
    json.dumps(result)


def test_health_summary_reuses_all_pipeline_types_and_analysis() -> None:
    service, fake_client = make_service()

    result = service.get_health_summary(days=7)

    assert set(result) == ENVELOPE_FIELDS
    assert result["available_days"] == 1
    assert result["data"]["schema_version"] == 1
    assert set(result["data"]["metrics"]) == {
        "sleep_minutes",
        "steps",
        "heart_rate_average",
        "resting_heart_rate",
        "hrv_rmssd",
    }
    assert fake_client is not None
    assert [call.args for call in fake_client.fetch_all.call_args_list] == [
        (data_type, START) for data_type in DATA_TYPES
    ]
    json.dumps(result)


@pytest.mark.parametrize("days", [0, 2, 5, 10, 15, 365, "seven", True])
def test_invalid_days_returns_validation_diagnostic_without_client_call(days) -> None:
    service, fake_client = make_service()

    result = service.get_steps(days=days)

    assert set(result) == ENVELOPE_FIELDS
    assert result["available_days"] == 0
    assert result["data"] == []
    assert result["diagnostics"] == {
        "validation": "days must be one of 14, 7, 3, or 1."
    }
    assert fake_client is not None
    fake_client.fetch_all.assert_not_called()


@pytest.mark.parametrize("days", [1, 3, 7, 14])
def test_metric_tools_accept_all_supported_fetch_windows(days: int) -> None:
    service, fake_client = make_service({"steps": FetchResult("steps", [])})

    result = service.get_steps(days=days)

    assert result["requested_days"] == days
    assert fake_client is not None
    fake_client.fetch_all.assert_called_once()


def test_empty_metric_data_returns_every_requested_date_as_missing() -> None:
    service, _ = make_service({"steps": FetchResult("steps", [])})

    result = service.get_steps(days=3)

    assert result["available_days"] == 0
    assert result["data"] == []
    assert result["missing_data"] == [
        "2026-07-16",
        "2026-07-17",
        "2026-07-18",
    ]


def test_single_data_type_failure_returns_safe_diagnostic() -> None:
    service, _ = make_service({
        "daily-heart-rate-variability": FetchResult(
            "daily-heart-rate-variability", [], "HTTP 403: permission denied"
        )
    })

    result = service.get_hrv(days=3)

    assert result["data"] == []
    assert result["diagnostics"] == {
        "daily-heart-rate-variability": "HTTP 403: permission denied"
    }


def test_summary_preserves_successful_types_when_one_type_fails() -> None:
    results = health_results()
    results["sleep"] = FetchResult("sleep", [], "HTTP 403: permission denied")
    service, _ = make_service(results)

    result = service.get_health_summary(days=7)

    assert result["available_days"] == 1
    assert result["data"]["metrics"]["steps"]["current_samples"] == 1
    assert result["diagnostics"] == {"sleep": "HTTP 403: permission denied"}


def test_summary_counts_zero_steps_as_available_data() -> None:
    proto_date = {"year": 2026, "month": 7, "day": 18}
    results = {
        data_type: FetchResult(data_type, []) for data_type in DATA_TYPES
    }
    results["steps"] = FetchResult("steps", [{
        "steps": {
            "interval": {"civilEndTime": {"date": proto_date}},
            "count": "0",
        }
    }])
    service, _ = make_service(results)

    result = service.get_health_summary(days=1)

    assert result["available_days"] == 1
    assert result["missing_data"] == []


def test_authentication_failure_returns_sanitized_envelope() -> None:
    def fail_authentication():
        raise AuthError(
            "本地 Google 授权不可用，请运行 python -m fitbit_health sync --days 1。"
        )

    service, _ = make_service(client_factory=fail_authentication)

    result = service.get_sleep(days=7)

    assert set(result) == ENVELOPE_FIELDS
    assert result["available_days"] == 0
    assert result["data"] == []
    assert result["missing_data"] == []
    assert "authentication" in result["diagnostics"]
    serialized = json.dumps(result)
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    assert "Authorization" not in serialized


def test_unexpected_client_failure_does_not_escape_tool_boundary() -> None:
    def fail_unexpectedly():
        raise RuntimeError("secret-client-value")

    service, _ = make_service(client_factory=fail_unexpectedly)

    result = service.get_steps(days=7)

    assert result["data"] == []
    assert result["diagnostics"] == {
        "internal": "Health data could not be loaded."
    }
    assert "secret-client-value" not in json.dumps(result)
