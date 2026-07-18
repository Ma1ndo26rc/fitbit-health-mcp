from dataclasses import dataclass
from datetime import date
import time
from typing import Callable

import requests


BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
DATA_SOURCE_FAMILY = "users/me/dataSourceFamilies/google-wearables"
FILTER_FIELDS = {
    "sleep": "sleep.interval.civil_end_time",
    "steps": "steps.interval.civil_end_time",
    "heart-rate": "heart_rate.sample_time.civil_time",
    "daily-resting-heart-rate": "daily_resting_heart_rate.date",
    "daily-heart-rate-variability": "daily_heart_rate_variability.date",
}
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class FetchResult:
    data_type: str
    data_points: list[dict]
    error: str | None = None


def build_filter(data_type: str, start_date: date) -> str:
    """Build an AIP-160 civil-date lower bound for a supported data type."""
    try:
        field = FILTER_FIELDS[data_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported Google Health data type: {data_type}") from exc
    return f'{field} >= "{start_date.isoformat()}"'


class GoogleHealthClient:
    """Small read-only client for reconciled Google Health data points."""

    def __init__(
        self,
        credentials,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self.session = session or requests.Session()
        self.sleeper = sleeper

    def fetch_all(self, data_type: str, start_date: date) -> FetchResult:
        url = f"{BASE_URL}/{data_type}/dataPoints:reconcile"
        params: dict[str, str | int] = {
            "dataSourceFamily": DATA_SOURCE_FAMILY,
            "filter": build_filter(data_type, start_date),
            "pageSize": 25 if data_type == "sleep" else 10_000,
        }
        points: list[dict] = []

        for _ in range(1_000):
            response = self._get_with_retry(url, params.copy())
            if response is None:
                return FetchResult(data_type, points, "network request failed")
            if response.status_code == 403:
                return FetchResult(data_type, points, "HTTP 403: permission denied")
            if response.status_code >= 400:
                return FetchResult(data_type, points, f"HTTP {response.status_code}: request failed")

            try:
                payload = response.json()
            except ValueError:
                return FetchResult(data_type, points, "invalid JSON response")
            if not isinstance(payload, dict):
                return FetchResult(data_type, points, "invalid JSON response")
            page_points = payload.get("dataPoints", [])
            if not isinstance(page_points, list):
                return FetchResult(data_type, points, "invalid dataPoints response")
            points.extend(item for item in page_points if isinstance(item, dict))

            next_token = payload.get("nextPageToken")
            if not next_token:
                return FetchResult(data_type, points)
            params["pageToken"] = str(next_token)

        return FetchResult(data_type, points, "pagination limit exceeded")

    def _get_with_retry(self, url: str, params: dict[str, str | int]):
        headers = {
            "Authorization": f"Bearer {self.credentials.token}",
            "Accept": "application/json",
        }
        for attempt in range(3):
            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=30,
                )
            except requests.RequestException:
                if attempt == 2:
                    return None
                self.sleeper(2**attempt)
                continue
            if response.status_code not in TRANSIENT_STATUSES or attempt == 2:
                return response
            self.sleeper(2**attempt)
        return None
