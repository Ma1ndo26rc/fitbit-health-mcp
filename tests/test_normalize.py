from datetime import date

from fitbit_health.client import FetchResult
from fitbit_health.normalize import normalize_results


def test_normalizes_all_supported_metrics_by_local_date() -> None:
    results = {
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
        "steps": FetchResult("steps", [
            {"steps": {"interval": {"civilEndTime": {"date": {"year": 2026, "month": 7, "day": 18}}}, "count": "1200"}},
            {"steps": {"interval": {"civilEndTime": {"date": {"year": 2026, "month": 7, "day": 18}}}, "count": "800"}},
        ]),
        "heart-rate": FetchResult("heart-rate", [
            {"heartRate": {"sampleTime": {"civilTime": {"date": {"year": 2026, "month": 7, "day": 18}}}, "beatsPerMinute": "60"}},
            {"heartRate": {"sampleTime": {"civilTime": {"date": {"year": 2026, "month": 7, "day": 18}}}, "beatsPerMinute": "70"}},
        ]),
        "daily-resting-heart-rate": FetchResult("daily-resting-heart-rate", [{
            "dailyRestingHeartRate": {"date": {"year": 2026, "month": 7, "day": 18}, "beatsPerMinute": "55"}
        }]),
        "daily-heart-rate-variability": FetchResult("daily-heart-rate-variability", [{
            "dailyHeartRateVariability": {
                "date": {"year": 2026, "month": 7, "day": 18},
                "averageHeartRateVariabilityMilliseconds": 62.5,
            }
        }]),
    }

    output = normalize_results(results, date(2026, 7, 18), date(2026, 7, 18))

    day = output["days"][0]
    assert output["schema_version"] == 1
    assert day["sleep"] == {
        "minutes_asleep": 420,
        "minutes_awake": 30,
        "deep_minutes": 80,
        "rem_minutes": 100,
        "light_minutes": 240,
        "start_time": "2026-07-17T15:00:00Z",
        "start_utc_offset": "28800s",
        "end_time": "2026-07-17T22:30:00Z",
        "end_utc_offset": "28800s",
    }
    assert day["steps"] == 2000
    assert day["heart_rate_average"] == 65.0
    assert day["resting_heart_rate"] == 55.0
    assert day["hrv_rmssd"] == 62.5
    assert all(day["availability"].values())


def test_preserves_missing_values_and_fetch_diagnostics() -> None:
    results = {
        "daily-heart-rate-variability": FetchResult(
            "daily-heart-rate-variability", [], "HTTP 403: permission denied"
        )
    }

    output = normalize_results(results, date(2026, 7, 17), date(2026, 7, 18))

    assert len(output["days"]) == 2
    assert output["days"][0]["steps"] is None
    assert output["days"][0]["hrv_rmssd"] is None
    assert output["diagnostics"] == {
        "daily-heart-rate-variability": "HTTP 403: permission denied"
    }


def test_ignores_points_outside_requested_range() -> None:
    results = {"daily-resting-heart-rate": FetchResult("daily-resting-heart-rate", [{
        "dailyRestingHeartRate": {"date": {"year": 2026, "month": 7, "day": 1}, "beatsPerMinute": "55"}
    }])}

    output = normalize_results(results, date(2026, 7, 18), date(2026, 7, 18))

    assert output["days"][0]["resting_heart_rate"] is None
