from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from fitbit_health.client import FetchResult


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _proto_date(value: Any) -> date | None:
    if not isinstance(value, dict):
        return None
    try:
        return date(int(value["year"]), int(value["month"]), int(value["day"]))
    except (KeyError, TypeError, ValueError):
        return None


def _duration_seconds(value: Any) -> float:
    if not isinstance(value, str) or not value.endswith("s"):
        return 0.0
    try:
        return float(value[:-1])
    except ValueError:
        return 0.0


def _interval_local_end_date(interval: Any) -> date | None:
    if not isinstance(interval, dict):
        return None
    civil = _proto_date((interval.get("civilEndTime") or {}).get("date"))
    if civil is not None:
        return civil
    raw_end = interval.get("endTime")
    if not isinstance(raw_end, str):
        return None
    try:
        physical_end = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (physical_end + timedelta(seconds=_duration_seconds(interval.get("endUtcOffset")))).date()


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _blank_day(day: date) -> dict:
    return {
        "date": day.isoformat(),
        "sleep": None,
        "steps": None,
        "heart_rate_average": None,
        "resting_heart_rate": None,
        "hrv_rmssd": None,
        "availability": {
            "sleep": False,
            "steps": False,
            "heart_rate_average": False,
            "resting_heart_rate": False,
            "hrv_rmssd": False,
        },
    }


def _normalize_sleep(result: FetchResult | None, days: dict[str, dict]) -> None:
    if result is None:
        return
    candidates: dict[str, list[tuple[bool, dict]]] = defaultdict(list)
    for point in result.data_points:
        sleep = point.get("sleep")
        if not isinstance(sleep, dict):
            continue
        interval = sleep.get("interval") or {}
        local_day = _interval_local_end_date(interval)
        if local_day is None or local_day.isoformat() not in days:
            continue
        summary = sleep.get("summary") or {}
        stages = {
            item.get("type"): _as_int(item.get("minutes"))
            for item in summary.get("stagesSummary", [])
            if isinstance(item, dict)
        }
        normalized = {
            "minutes_asleep": _as_int(summary.get("minutesAsleep")),
            "minutes_awake": _as_int(summary.get("minutesAwake")),
            "deep_minutes": stages.get("DEEP"),
            "rem_minutes": stages.get("REM"),
            "light_minutes": stages.get("LIGHT"),
            "start_time": interval.get("startTime"),
            "start_utc_offset": interval.get("startUtcOffset"),
            "end_time": interval.get("endTime"),
            "end_utc_offset": interval.get("endUtcOffset"),
        }
        is_main = bool((sleep.get("metadata") or {}).get("main"))
        candidates[local_day.isoformat()].append((is_main, normalized))

    for key, sessions in candidates.items():
        chosen = max(
            sessions,
            key=lambda item: (item[0], item[1].get("minutes_asleep") or 0),
        )[1]
        days[key]["sleep"] = chosen


def _normalize_steps(result: FetchResult | None, days: dict[str, dict]) -> None:
    if result is None:
        return
    totals: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    for point in result.data_points:
        steps = point.get("steps")
        if not isinstance(steps, dict):
            continue
        local_day = _interval_local_end_date(steps.get("interval"))
        count = _as_int(steps.get("count"))
        if local_day is None or count is None:
            continue
        key = local_day.isoformat()
        if key in days:
            totals[key] += count
            seen.add(key)
    for key in seen:
        days[key]["steps"] = totals[key]


def _normalize_heart_rate(result: FetchResult | None, days: dict[str, dict]) -> None:
    if result is None:
        return
    samples: dict[str, list[float]] = defaultdict(list)
    for point in result.data_points:
        heart_rate = point.get("heartRate")
        if not isinstance(heart_rate, dict):
            continue
        civil_date = _proto_date(((heart_rate.get("sampleTime") or {}).get("civilTime") or {}).get("date"))
        value = _as_float(heart_rate.get("beatsPerMinute"))
        if civil_date is not None and value is not None and civil_date.isoformat() in days:
            samples[civil_date.isoformat()].append(value)
    for key, values in samples.items():
        days[key]["heart_rate_average"] = round(sum(values) / len(values), 2)


def _normalize_daily(
    result: FetchResult | None,
    days: dict[str, dict],
    payload_name: str,
    value_name: str,
    output_name: str,
) -> None:
    if result is None:
        return
    for point in result.data_points:
        payload = point.get(payload_name)
        if not isinstance(payload, dict):
            continue
        local_day = _proto_date(payload.get("date"))
        value = _as_float(payload.get(value_name))
        if local_day is not None and value is not None and local_day.isoformat() in days:
            days[local_day.isoformat()][output_name] = value


def normalize_results(
    results: dict[str, FetchResult],
    start_date: date,
    end_date: date,
) -> dict:
    """Normalize supported reconciled data points into a daily schema."""
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    days = {day.isoformat(): _blank_day(day) for day in _date_range(start_date, end_date)}

    _normalize_sleep(results.get("sleep"), days)
    _normalize_steps(results.get("steps"), days)
    _normalize_heart_rate(results.get("heart-rate"), days)
    _normalize_daily(
        results.get("daily-resting-heart-rate"),
        days,
        "dailyRestingHeartRate",
        "beatsPerMinute",
        "resting_heart_rate",
    )
    _normalize_daily(
        results.get("daily-heart-rate-variability"),
        days,
        "dailyHeartRateVariability",
        "averageHeartRateVariabilityMilliseconds",
        "hrv_rmssd",
    )

    for day in days.values():
        for name in day["availability"]:
            day["availability"][name] = day[name] is not None

    diagnostics = {
        name: result.error
        for name, result in results.items()
        if result.error is not None
    }
    return {
        "schema_version": 1,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": list(days.values()),
        "diagnostics": diagnostics,
    }
