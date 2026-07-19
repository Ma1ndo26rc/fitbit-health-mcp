from datetime import datetime, timedelta
import math
from statistics import mean, pstdev
from typing import Any


MINIMUM_TREND_SAMPLES = 3
MINUTES_PER_DAY = 24 * 60


def _rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def analyze_metric(values: list[float | int | None], recent_days: int = 7) -> dict:
    """Compare a recent window with the preceding available baseline."""
    numeric = [float(value) for value in values if value is not None]
    current = [float(value) for value in values[-recent_days:] if value is not None]
    baseline = [float(value) for value in values[:-recent_days] if value is not None]
    current_mean = _rounded_mean(current)
    baseline_mean = _rounded_mean(baseline)
    enough = (
        len(current) >= MINIMUM_TREND_SAMPLES
        and len(baseline) >= MINIMUM_TREND_SAMPLES
    )
    absolute_change = (
        round(current_mean - baseline_mean, 2)
        if enough and current_mean is not None and baseline_mean is not None
        else None
    )
    percent_change = (
        round(absolute_change / baseline_mean * 100, 2)
        if absolute_change is not None and baseline_mean
        else None
    )
    return {
        "current_mean": current_mean,
        "current_samples": len(current),
        "window_mean": _rounded_mean(numeric),
        "window_samples": len(numeric),
        "baseline_mean": baseline_mean,
        "baseline_samples": len(baseline),
        "absolute_change": absolute_change,
        "percent_change": percent_change,
    }


def clock_stddev_minutes(minutes: list[int | float]) -> float | None:
    """Return circular clock-time standard deviation in minutes."""
    if len(minutes) < MINIMUM_TREND_SAMPLES:
        return None
    angles = [2 * math.pi * (value % MINUTES_PER_DAY) / MINUTES_PER_DAY for value in minutes]
    mean_angle = math.atan2(
        sum(math.sin(angle) for angle in angles),
        sum(math.cos(angle) for angle in angles),
    )
    mean_minutes = (mean_angle % (2 * math.pi)) * MINUTES_PER_DAY / (2 * math.pi)
    differences = [
        ((value - mean_minutes + MINUTES_PER_DAY / 2) % MINUTES_PER_DAY)
        - MINUTES_PER_DAY / 2
        for value in minutes
    ]
    return round(pstdev(differences), 2)


def _duration_seconds(value: Any) -> float:
    if not isinstance(value, str) or not value.endswith("s"):
        return 0.0
    try:
        return float(value[:-1])
    except ValueError:
        return 0.0


def _local_clock_minutes(timestamp: Any, utc_offset: Any) -> int | None:
    if not isinstance(timestamp, str):
        return None
    try:
        physical = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    local = physical + timedelta(seconds=_duration_seconds(utc_offset))
    return local.hour * 60 + local.minute


def analyze(document: dict) -> dict:
    """Create traceable trend statistics from a normalized document."""
    days = document.get("days", [])
    getters = {
        "sleep_minutes": lambda day: (day.get("sleep") or {}).get("minutes_asleep"),
        "steps": lambda day: day.get("steps"),
        "heart_rate_average": lambda day: day.get("heart_rate_average"),
        "resting_heart_rate": lambda day: day.get("resting_heart_rate"),
        "hrv_rmssd": lambda day: day.get("hrv_rmssd"),
    }
    metrics = {
        name: analyze_metric([getter(day) for day in days])
        for name, getter in getters.items()
    }

    sleep_starts: list[int] = []
    wake_times: list[int] = []
    for day in days:
        sleep = day.get("sleep") or {}
        start = _local_clock_minutes(
            sleep.get("start_time"), sleep.get("start_utc_offset")
        )
        end = _local_clock_minutes(
            sleep.get("end_time"), sleep.get("end_utc_offset")
        )
        if start is not None and end is not None:
            sleep_starts.append(start)
            wake_times.append(end)

    return {
        "schema_version": 1,
        "metrics": metrics,
        "sleep_regularity": {
            "samples": len(sleep_starts),
            "sleep_start_stddev_minutes": clock_stddev_minutes(sleep_starts),
            "wake_time_stddev_minutes": clock_stddev_minutes(wake_times),
        },
        "data_quality": {
            "days_requested": len(days),
            "days_with_sleep": sum(day.get("sleep") is not None for day in days),
            "diagnostics": document.get("diagnostics", {}),
        },
    }
