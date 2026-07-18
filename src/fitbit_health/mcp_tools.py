from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fitbit_health.analytics import analyze
from fitbit_health.auth import AuthError, load_saved_credentials
from fitbit_health.client import GoogleHealthClient
from fitbit_health.config import ConfigError, SCOPES, find_installed_credentials
from fitbit_health.fetch_window import FETCH_DAYS_ERROR, is_allowed_fetch_days
from fitbit_health.normalize import normalize_results
from fitbit_health.pipeline import DATA_TYPES


METRIC_TOOLS = {
    "sleep": ("sleep", "sleep"),
    "steps": ("steps", "steps"),
    "heart-rate": ("heart_rate_average", "heart_rate_average"),
    "daily-resting-heart-rate": ("resting_heart_rate", "resting_heart_rate"),
    "daily-heart-rate-variability": ("hrv_rmssd", "hrv_rmssd"),
}


class HealthMCPService:
    """Compose existing health-pipeline APIs into MCP-safe JSON results."""

    def __init__(
        self,
        root: Path,
        client_factory: Callable[[], GoogleHealthClient] | None = None,
        today_factory: Callable[[], date] = date.today,
    ) -> None:
        self.root = root
        self._client_factory = client_factory or self._make_default_client
        self._today_factory = today_factory

    def _make_default_client(self) -> GoogleHealthClient:
        find_installed_credentials(self.root)
        credentials = load_saved_credentials(
            self.root / ".private" / "token.json",
            SCOPES,
        )
        return GoogleHealthClient(credentials)

    @staticmethod
    def _empty_result(days: Any, data: Any, diagnostics: dict) -> dict[str, Any]:
        return {
            "requested_days": days if isinstance(days, int) and not isinstance(days, bool) else 0,
            "available_days": 0,
            "data": data,
            "missing_data": [],
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _valid_days(days: Any) -> bool:
        return is_allowed_fetch_days(days)

    def _range(self, days: int) -> tuple[date, date]:
        end_date = self._today_factory()
        return end_date - timedelta(days=days - 1), end_date

    def _metric(self, data_type: str, days: int) -> dict[str, Any]:
        if not self._valid_days(days):
            return self._empty_result(
                days,
                [],
                {"validation": FETCH_DAYS_ERROR},
            )

        try:
            client = self._client_factory()
            start_date, end_date = self._range(days)
            fetched = client.fetch_all(data_type, start_date)
            normalized = normalize_results(
                {data_type: fetched}, start_date, end_date
            )
        except (AuthError, ConfigError):
            return self._empty_result(
                days,
                [],
                {
                    "authentication": (
                        "Google authorization is unavailable. Run "
                        "python -m fitbit_health sync --days 1 in a terminal."
                    )
                },
            )
        except Exception:
            return self._empty_result(
                days,
                [],
                {"internal": "Health data could not be loaded."},
            )

        source_field, output_field = METRIC_TOOLS[data_type]
        data: list[dict[str, Any]] = []
        missing: list[str] = []
        for day in normalized["days"]:
            value = day[source_field]
            if value is None:
                missing.append(day["date"])
                continue
            if data_type == "sleep":
                data.append({
                    "date": day["date"],
                    "minutes_asleep": value.get("minutes_asleep"),
                    "minutes_awake": value.get("minutes_awake"),
                    "deep_minutes": value.get("deep_minutes"),
                    "rem_minutes": value.get("rem_minutes"),
                    "light_minutes": value.get("light_minutes"),
                    "start_time": value.get("start_time"),
                    "end_time": value.get("end_time"),
                })
            else:
                data.append({"date": day["date"], output_field: value})

        return {
            "requested_days": days,
            "available_days": len(data),
            "data": data,
            "missing_data": missing,
            "diagnostics": normalized["diagnostics"],
        }

    def get_sleep(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily sleep data."""
        return self._metric("sleep", days)

    def get_steps(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily step counts."""
        return self._metric("steps", days)

    def get_heart_rate(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily average heart rate."""
        return self._metric("heart-rate", days)

    def get_resting_heart_rate(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily resting heart rate."""
        return self._metric("daily-resting-heart-rate", days)

    def get_hrv(self, days: int = 7) -> dict[str, Any]:
        """Return normalized daily HRV RMSSD."""
        return self._metric("daily-heart-rate-variability", days)

    def get_health_summary(self, days: int = 7) -> dict[str, Any]:
        """Return existing health analysis across all supported metrics."""
        if not self._valid_days(days):
            return self._empty_result(
                days,
                {},
                {"validation": FETCH_DAYS_ERROR},
            )

        try:
            client = self._client_factory()
            start_date, end_date = self._range(days)
            results = {
                data_type: client.fetch_all(data_type, start_date)
                for data_type in DATA_TYPES
            }
            normalized = normalize_results(results, start_date, end_date)
            analysis = analyze(normalized)
        except (AuthError, ConfigError):
            return self._empty_result(
                days,
                {},
                {
                    "authentication": (
                        "Google authorization is unavailable. Run "
                        "python -m fitbit_health sync --days 1 in a terminal."
                    )
                },
            )
        except Exception:
            return self._empty_result(
                days,
                {},
                {"internal": "Health data could not be loaded."},
            )

        available = []
        missing = []
        for item in normalized["days"]:
            has_data = any(
                value is not None
                for value in (
                    item["sleep"],
                    item["steps"],
                    item["heart_rate_average"],
                    item["resting_heart_rate"],
                    item["hrv_rmssd"],
                )
            )
            (available if has_data else missing).append(item["date"])

        return {
            "requested_days": days,
            "available_days": len(available),
            "data": analysis,
            "missing_data": missing,
            "diagnostics": normalized["diagnostics"],
        }
