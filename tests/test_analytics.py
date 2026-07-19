from fitbit_health.analytics import analyze, analyze_metric, clock_stddev_minutes


def test_compares_recent_seven_days_with_prior_available_days() -> None:
    values = [50.0] * 7 + [60.0] * 7

    result = analyze_metric(values, recent_days=7)

    assert result == {
        "current_mean": 60.0,
        "current_samples": 7,
        "window_mean": 55.0,
        "window_samples": 14,
        "baseline_mean": 50.0,
        "baseline_samples": 7,
        "absolute_change": 10.0,
        "percent_change": 20.0,
    }
    assert "thirty_day_mean" not in result
    assert "thirty_day_samples" not in result


def test_omits_change_when_samples_are_insufficient() -> None:
    result = analyze_metric([None] * 29 + [10.0], recent_days=7)

    assert result["current_samples"] == 1
    assert result["absolute_change"] is None
    assert result["percent_change"] is None


def test_keeps_absolute_change_but_omits_percent_for_zero_baseline() -> None:
    result = analyze_metric([0.0] * 23 + [10.0] * 7, recent_days=7)

    assert result["absolute_change"] == 10.0
    assert result["percent_change"] is None


def test_clock_stddev_handles_times_around_midnight() -> None:
    result = clock_stddev_minutes([23 * 60 + 50, 0, 10])

    assert result == 8.16


def test_analyze_reports_metrics_regularity_and_data_quality() -> None:
    days = []
    for index in range(14):
        day = {
            "date": f"2026-07-{index + 1:02d}",
            "sleep": None,
            "steps": 1000 + index,
            "heart_rate_average": 65.0,
            "resting_heart_rate": 55.0,
            "hrv_rmssd": 60.0,
        }
        if index >= 11:
            minute_shift = (index - 12) * 10
            day["sleep"] = {
                "minutes_asleep": 420,
                "start_time": "2026-07-01T15:00:00Z",
                "start_utc_offset": f"{28800 + minute_shift * 60}s",
                "end_time": "2026-07-01T22:30:00Z",
                "end_utc_offset": f"{28800 + minute_shift * 60}s",
            }
        days.append(day)

    result = analyze({
        "schema_version": 1,
        "days": days,
        "diagnostics": {"sleep": "partial"},
    })

    assert result["schema_version"] == 1
    assert result["metrics"]["steps"]["current_samples"] == 7
    assert result["metrics"]["steps"]["window_mean"] == 1006.5
    assert result["metrics"]["steps"]["window_samples"] == 14
    assert result["sleep_regularity"]["samples"] == 3
    assert result["sleep_regularity"]["sleep_start_stddev_minutes"] == 8.16
    assert result["data_quality"]["days_requested"] == 14
    assert result["data_quality"]["days_with_sleep"] == 3
    assert result["data_quality"]["diagnostics"] == {"sleep": "partial"}
