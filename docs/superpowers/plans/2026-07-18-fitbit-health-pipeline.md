# Fitbit Health Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python CLI that authenticates with Google Health, downloads 30 days of Fitbit Air data, normalizes it, calculates traceable trends, and writes private JSON and Chinese Markdown reports.

**Architecture:** A desktop OAuth adapter supplies credentials to a small HTTP client. Focused pure modules normalize API payloads, calculate statistics, and render reports; a CLI composes them while keeping credentials and health data out of Git.

**Tech Stack:** Python 3.12, `google-auth`, `google-auth-oauthlib`, `requests`, `pytest`, Google Health REST API v4.

## Global Constraints

- Run locally on Windows with Python 3.12 or newer.
- Request only `activity_and_fitness.readonly`, `sleep.readonly`, and `health_metrics_and_measurements.readonly` scopes.
- Prefer an OAuth credential whose JSON root contains `installed`; never select the `web` credential for the CLI.
- Never log client secrets, access tokens, refresh tokens, Authorization headers, or complete raw API responses.
- Treat absent measurements as `None`; never impute health values.
- Reports describe data trends only and must not diagnose, prescribe treatment, or create an unvalidated recovery score.
- MCP, a web dashboard, cloud scheduling, and LLM calls are out of scope.

## File Map

- `pyproject.toml`: package metadata, runtime dependencies, pytest settings, and the `fitbit-health` console command.
- `src/fitbit_health/config.py`: paths, scopes, credential discovery, and configuration errors.
- `src/fitbit_health/auth.py`: desktop OAuth, token persistence, refresh, and sanitized auth errors.
- `src/fitbit_health/client.py`: Google Health v4 requests, pagination, retry policy, and per-data-type diagnostics.
- `src/fitbit_health/normalize.py`: convert API payloads into versioned daily records.
- `src/fitbit_health/analytics.py`: sample-aware 7-day and 30-day statistics.
- `src/fitbit_health/report.py`: JSON serialization and Chinese Markdown rendering.
- `src/fitbit_health/pipeline.py`: orchestration and output paths.
- `src/fitbit_health/__main__.py`: argparse CLI and exit codes.
- `tests/fixtures/`: synthetic Google Health payloads only.
- `tests/test_*.py`: focused unit and end-to-end tests.
- `README.md`: installation, first authorization, sync, privacy, and troubleshooting.

---

### Task 1: Package Skeleton and Safe Credential Discovery

**Files:**
- Create: `pyproject.toml`
- Create: `src/fitbit_health/__init__.py`
- Create: `src/fitbit_health/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: workspace files matching `client_secret_*.json`.
- Produces: `SCOPES: tuple[str, ...]`, `ConfigError`, and `find_installed_credentials(root: Path) -> Path`.

- [ ] **Step 1: Write the failing credential-discovery tests**

```python
# tests/test_config.py
import json
from pathlib import Path

import pytest

from fitbit_health.config import ConfigError, find_installed_credentials


def write_client(path: Path, kind: str) -> None:
    path.write_text(json.dumps({kind: {"client_id": "test", "client_secret": "secret"}}), encoding="utf-8")


def test_prefers_installed_client_over_web_client(tmp_path: Path) -> None:
    write_client(tmp_path / "client_secret_web.json", "web")
    installed = tmp_path / "client_secret_desktop.json"
    write_client(installed, "installed")
    assert find_installed_credentials(tmp_path) == installed


def test_rejects_missing_installed_client(tmp_path: Path) -> None:
    write_client(tmp_path / "client_secret_web.json", "web")
    with pytest.raises(ConfigError, match="桌面设备"):
        find_installed_credentials(tmp_path)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_config.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'fitbit_health'`.

- [ ] **Step 3: Add package metadata and minimal implementation**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "fitbit-health"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "google-auth>=2.40,<3",
  "google-auth-oauthlib>=1.2,<2",
  "requests>=2.32,<3",
]

[project.optional-dependencies]
test = ["pytest>=7.4,<9"]

[project.scripts]
fitbit-health = "fitbit_health.__main__:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```python
# src/fitbit_health/__init__.py
"""Local Google Health data pipeline."""
```

```python
# src/fitbit_health/config.py
import json
from pathlib import Path

SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
)


class ConfigError(RuntimeError):
    pass


def find_installed_credentials(root: Path) -> Path:
    installed: list[Path] = []
    for path in sorted(root.glob("client_secret_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("installed"), dict):
            installed.append(path)
    if not installed:
        raise ConfigError("未找到 Google OAuth 桌面设备凭据 JSON。")
    if len(installed) > 1:
        raise ConfigError("找到多个桌面设备凭据，请只保留一个 client_secret_*.json。")
    return installed[0]
```

- [ ] **Step 4: Install and verify GREEN**

Run: `python -m pip install -e ".[test]"`

Expected: editable package installation succeeds.

Run: `python -m pytest tests/test_config.py -v`

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```text
git add pyproject.toml src/fitbit_health/__init__.py src/fitbit_health/config.py tests/test_config.py
git commit -m "feat: add safe desktop credential discovery"
```

---

### Task 2: Desktop OAuth and Private Token Persistence

**Files:**
- Create: `src/fitbit_health/auth.py`
- Create: `tests/test_auth.py`

**Interfaces:**
- Consumes: credential path, token path, and `SCOPES`.
- Produces: `load_credentials(client_path: Path, token_path: Path, scopes: tuple[str, ...]) -> google.oauth2.credentials.Credentials`.

- [ ] **Step 1: Write failing tests for token reuse and refresh**

```python
# tests/test_auth.py
from pathlib import Path
from unittest.mock import Mock

from fitbit_health.auth import ensure_private_file, resolve_credentials


def test_reuses_valid_credentials_without_browser(tmp_path: Path) -> None:
    credentials = Mock(valid=True, expired=False)
    flow_factory = Mock()
    assert resolve_credentials(credentials, flow_factory, Mock()) is credentials
    flow_factory.assert_not_called()


def test_refreshes_expired_credentials_with_refresh_token(tmp_path: Path) -> None:
    credentials = Mock(valid=False, expired=True, refresh_token="refresh")
    request = Mock()
    resolved = resolve_credentials(credentials, Mock(), request)
    credentials.refresh.assert_called_once_with(request)
    assert resolved is credentials


def test_creates_token_parent_and_hides_file_on_windows(tmp_path: Path) -> None:
    token_path = tmp_path / "private" / "token.json"
    token_path.parent.mkdir()
    token_path.write_text("{}", encoding="utf-8")
    ensure_private_file(token_path)
    assert token_path.exists()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_auth.py -v`

Expected: import fails because `fitbit_health.auth` does not exist.

- [ ] **Step 3: Implement the OAuth adapter**

```python
# src/fitbit_health/auth.py
import os
from pathlib import Path
from typing import Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


class AuthError(RuntimeError):
    pass


def resolve_credentials(credentials, flow_factory: Callable, request):
    if credentials is not None and credentials.valid:
        return credentials
    if credentials is not None and credentials.expired and credentials.refresh_token:
        credentials.refresh(request)
        return credentials
    flow = flow_factory()
    return flow.run_local_server(host="localhost", port=0, open_browser=True, prompt="consent")


def ensure_private_file(path: Path) -> None:
    if os.name == "nt" and path.exists():
        os.system(f'attrib +H "{path}"')


def load_credentials(client_path: Path, token_path: Path, scopes: tuple[str, ...]) -> Credentials:
    existing = None
    if token_path.exists():
        try:
            existing = Credentials.from_authorized_user_file(str(token_path), scopes)
        except (OSError, ValueError) as exc:
            raise AuthError("本地 token 文件无效，请移走后重新授权。") from exc

    def make_flow():
        return InstalledAppFlow.from_client_secrets_file(str(client_path), scopes)

    try:
        credentials = resolve_credentials(existing, make_flow, Request())
    except Exception as exc:
        raise AuthError("Google 授权失败；请检查测试用户、scopes 和网络连接。") from exc
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    ensure_private_file(token_path)
    return credentials
```

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_auth.py -v`

Expected: `3 passed` and no browser opens during tests.

- [ ] **Step 5: Commit**

```text
git add src/fitbit_health/auth.py tests/test_auth.py
git commit -m "feat: add desktop OAuth token lifecycle"
```

---

### Task 3: Google Health REST Client with Pagination and Bounded Retry

**Files:**
- Create: `src/fitbit_health/client.py`
- Create: `tests/test_client.py`

**Interfaces:**
- Consumes: `Credentials`, data type name, start date, and optional data source family.
- Produces: `FetchResult(data_type: str, data_points: list[dict], error: str | None)` and `GoogleHealthClient.fetch_all(data_type: str, start_date: date) -> FetchResult`.

- [ ] **Step 1: Write failing pagination and partial-failure tests**

```python
# tests/test_client.py
from datetime import date
from unittest.mock import Mock

from fitbit_health.client import GoogleHealthClient


def response(payload: dict, status: int = 200) -> Mock:
    item = Mock(status_code=status)
    item.json.return_value = payload
    item.raise_for_status.side_effect = None
    return item


def test_fetch_all_follows_next_page_token() -> None:
    session = Mock()
    session.get.side_effect = [
        response({"dataPoints": [{"name": "one"}], "nextPageToken": "next"}),
        response({"dataPoints": [{"name": "two"}], "nextPageToken": ""}),
    ]
    client = GoogleHealthClient(Mock(token="token"), session=session, sleeper=lambda _: None)
    result = client.fetch_all("sleep", date(2026, 7, 1))
    assert [point["name"] for point in result.data_points] == ["one", "two"]
    assert session.get.call_count == 2


def test_fetch_all_returns_sanitized_error_for_forbidden() -> None:
    session = Mock()
    forbidden = Mock(status_code=403, text="sensitive body")
    session.get.return_value = forbidden
    client = GoogleHealthClient(Mock(token="token"), session=session, sleeper=lambda _: None)
    result = client.fetch_all("daily-heart-rate-variability", date(2026, 7, 1))
    assert result.data_points == []
    assert result.error == "HTTP 403: permission denied"
    assert "sensitive" not in result.error
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_client.py -v`

Expected: import fails because `fitbit_health.client` does not exist.

- [ ] **Step 3: Implement the minimal client**

```python
# src/fitbit_health/client.py
from dataclasses import dataclass
from datetime import date
import time

import requests

BASE_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
WEARABLE_TYPES = {"sleep", "steps", "heart-rate", "daily-resting-heart-rate", "daily-heart-rate-variability"}


@dataclass(frozen=True)
class FetchResult:
    data_type: str
    data_points: list[dict]
    error: str | None = None


class GoogleHealthClient:
    def __init__(self, credentials, session=None, sleeper=time.sleep):
        self.credentials = credentials
        self.session = session or requests.Session()
        self.sleeper = sleeper

    def fetch_all(self, data_type: str, start_date: date) -> FetchResult:
        url = f"{BASE_URL}/{data_type}/dataPoints:reconcile"
        snake = data_type.replace("-", "_")
        params = {
            "dataSourceFamily": "users/me/dataSourceFamilies/google-wearables",
            "filter": f'{snake}.interval.civil_end_time >= "{start_date.isoformat()}"',
            "pageSize": 1000,
        }
        points: list[dict] = []
        for page in range(100):
            response = self._get_with_retry(url, params)
            if response.status_code == 403:
                return FetchResult(data_type, [], "HTTP 403: permission denied")
            if response.status_code >= 400:
                return FetchResult(data_type, [], f"HTTP {response.status_code}: request failed")
            payload = response.json()
            points.extend(payload.get("dataPoints", []))
            token = payload.get("nextPageToken")
            if not token:
                return FetchResult(data_type, points)
            params["pageToken"] = token
        return FetchResult(data_type, points, "pagination limit exceeded")

    def _get_with_retry(self, url: str, params: dict):
        headers = {"Authorization": f"Bearer {self.credentials.token}", "Accept": "application/json"}
        for attempt in range(3):
            response = self.session.get(url, headers=headers, params=params, timeout=30)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                return response
            self.sleeper(2**attempt)
        raise AssertionError("unreachable")
```

- [ ] **Step 4: Add filter-shape tests for daily sample types, then correct the filter builder**

Add a parameterized test asserting interval types use `.interval.civil_end_time` while daily types use `.date >= "YYYY-MM-DD"`. Extract `build_filter(data_type, start_date)` and map:

```python
FILTER_FIELDS = {
    "sleep": "sleep.interval.civil_end_time",
    "steps": "steps.interval.civil_end_time",
    "heart-rate": "heart_rate.sample_time.civil_time",
    "daily-resting-heart-rate": "daily_resting_heart_rate.date",
    "daily-heart-rate-variability": "daily_heart_rate_variability.date",
}


def build_filter(data_type: str, start_date: date) -> str:
    return f'{FILTER_FIELDS[data_type]} >= "{start_date.isoformat()}"'
```

Run: `python -m pytest tests/test_client.py -v`

Expected: all client tests pass, including retry count and sanitized errors.

- [ ] **Step 5: Commit**

```text
git add src/fitbit_health/client.py tests/test_client.py
git commit -m "feat: add resilient Google Health client"
```

---

### Task 4: Versioned Daily Normalization

**Files:**
- Create: `src/fitbit_health/normalize.py`
- Create: `tests/fixtures/sleep.json`
- Create: `tests/fixtures/steps.json`
- Create: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `dict[str, FetchResult]` and inclusive date range.
- Produces: `normalize_results(results, start_date, end_date) -> dict` with schema version `1` and one record per calendar date.

- [ ] **Step 1: Add synthetic fixtures and failing normalization tests**

```python
# tests/test_normalize.py
from datetime import date

from fitbit_health.client import FetchResult
from fitbit_health.normalize import normalize_results


def test_sleep_belongs_to_session_end_date_and_sums_stages() -> None:
    sleep = {"sleep": {"interval": {"endTime": "2026-07-18T06:30:00+08:00"}, "summary": {
        "minutesAsleep": "420",
        "stagesSummary": [{"type": "DEEP", "minutes": "80"}, {"type": "REM", "minutes": "100"}],
    }}}
    output = normalize_results({"sleep": FetchResult("sleep", [sleep])}, date(2026, 7, 18), date(2026, 7, 18))
    day = output["days"][0]
    assert day["date"] == "2026-07-18"
    assert day["sleep"]["minutes_asleep"] == 420
    assert day["sleep"]["deep_minutes"] == 80
    assert day["sleep"]["rem_minutes"] == 100


def test_missing_measurements_remain_none() -> None:
    output = normalize_results({}, date(2026, 7, 18), date(2026, 7, 18))
    assert output["days"][0]["steps"] is None
    assert output["days"][0]["resting_heart_rate"] is None
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_normalize.py -v`

Expected: import fails because `fitbit_health.normalize` does not exist.

- [ ] **Step 3: Implement daily skeleton and sleep/steps normalization**

```python
# src/fitbit_health/normalize.py
from datetime import date, datetime, timedelta

from fitbit_health.client import FetchResult


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def normalize_results(results: dict[str, FetchResult], start_date: date, end_date: date) -> dict:
    days = {day.isoformat(): {
        "date": day.isoformat(), "sleep": None, "steps": None,
        "heart_rate_average": None, "resting_heart_rate": None, "hrv_rmssd": None,
    } for day in date_range(start_date, end_date)}

    for point in results.get("sleep", FetchResult("sleep", [])).data_points:
        sleep = point.get("sleep", {})
        end = sleep.get("interval", {}).get("endTime")
        if not end:
            continue
        key = datetime.fromisoformat(end.replace("Z", "+00:00")).date().isoformat()
        if key not in days:
            continue
        summary = sleep.get("summary", {})
        stages = {item.get("type"): int(item.get("minutes", 0)) for item in summary.get("stagesSummary", [])}
        days[key]["sleep"] = {
            "minutes_asleep": int(summary.get("minutesAsleep", 0)),
            "deep_minutes": stages.get("DEEP"),
            "rem_minutes": stages.get("REM"),
            "start_time": sleep.get("interval", {}).get("startTime"),
            "end_time": end,
        }

    for point in results.get("steps", FetchResult("steps", [])).data_points:
        steps = point.get("steps", {})
        civil = steps.get("interval", {}).get("civilEndTime", {}).get("date", {})
        if {"year", "month", "day"} <= civil.keys():
            key = date(civil["year"], civil["month"], civil["day"]).isoformat()
            if key in days:
                days[key]["steps"] = (days[key]["steps"] or 0) + int(steps.get("count", 0))

    diagnostics = {name: result.error for name, result in results.items() if result.error}
    return {"schema_version": 1, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "days": list(days.values()), "diagnostics": diagnostics}
```

- [ ] **Step 4: Add failing tests and handlers for heart rate, resting heart rate, and HRV**

Tests must assert sample heart rates are averaged per day, daily resting heart rate is preserved, HRV uses the API's RMSSD value, and an errored data type appears in `diagnostics`. Implement small private extractors `_normalize_heart_rate`, `_normalize_resting_heart_rate`, and `_normalize_hrv` called by `normalize_results`.

Run: `python -m pytest tests/test_normalize.py -v`

Expected: all normalization tests pass with no real health data in fixtures.

- [ ] **Step 5: Commit**

```text
git add src/fitbit_health/normalize.py tests/fixtures tests/test_normalize.py
git commit -m "feat: normalize Google Health data by day"
```

---

### Task 5: Sample-Aware Trend Analytics

**Files:**
- Create: `src/fitbit_health/analytics.py`
- Create: `tests/test_analytics.py`

**Interfaces:**
- Consumes: normalized document from Task 4.
- Produces: `analyze(document: dict) -> dict` with per-metric current window, baseline, absolute change, percentage change, and sample counts.

- [ ] **Step 1: Write failing statistical tests**

```python
# tests/test_analytics.py
from fitbit_health.analytics import analyze_metric


def test_compares_recent_seven_days_with_prior_available_days() -> None:
    values = [50.0] * 23 + [60.0] * 7
    result = analyze_metric(values, recent_days=7)
    assert result == {
        "current_mean": 60.0,
        "current_samples": 7,
        "baseline_mean": 50.0,
        "baseline_samples": 23,
        "absolute_change": 10.0,
        "percent_change": 20.0,
    }


def test_omits_change_when_baseline_is_zero_or_insufficient() -> None:
    result = analyze_metric([None] * 29 + [10.0], recent_days=7)
    assert result["current_samples"] == 1
    assert result["absolute_change"] is None
    assert result["percent_change"] is None
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_analytics.py -v`

Expected: import fails because `fitbit_health.analytics` does not exist.

- [ ] **Step 3: Implement minimal sample-aware statistics**

```python
# src/fitbit_health/analytics.py
from statistics import mean, pstdev


def rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def analyze_metric(values: list[float | None], recent_days: int = 7) -> dict:
    current = [float(v) for v in values[-recent_days:] if v is not None]
    baseline = [float(v) for v in values[:-recent_days] if v is not None]
    current_mean = rounded_mean(current)
    baseline_mean = rounded_mean(baseline)
    enough = len(current) >= 3 and len(baseline) >= 3
    absolute = round(current_mean - baseline_mean, 2) if enough else None
    percent = round(absolute / baseline_mean * 100, 2) if enough and baseline_mean else None
    return {
        "current_mean": current_mean, "current_samples": len(current),
        "baseline_mean": baseline_mean, "baseline_samples": len(baseline),
        "absolute_change": absolute, "percent_change": percent,
    }


def analyze(document: dict) -> dict:
    days = document["days"]
    getters = {
        "sleep_minutes": lambda day: (day.get("sleep") or {}).get("minutes_asleep"),
        "steps": lambda day: day.get("steps"),
        "heart_rate_average": lambda day: day.get("heart_rate_average"),
        "resting_heart_rate": lambda day: day.get("resting_heart_rate"),
        "hrv_rmssd": lambda day: day.get("hrv_rmssd"),
    }
    metrics = {name: analyze_metric([getter(day) for day in days]) for name, getter in getters.items()}
    sleep_midpoints = []
    for day in days:
        sleep = day.get("sleep") or {}
        if sleep.get("start_time") and sleep.get("end_time"):
            sleep_midpoints.append(1.0)
        else:
            sleep_midpoints.append(None)
    return {"schema_version": 1, "metrics": metrics, "data_quality": {
        "days_requested": len(days),
        "days_with_sleep": sum(day.get("sleep") is not None for day in days),
        "diagnostics": document.get("diagnostics", {}),
    }}
```

- [ ] **Step 4: Add and pass sleep-regularity tests**

Add tests using ISO timestamps around midnight. Implement circular-clock conversion and report `sleep_start_stddev_minutes` and `wake_time_stddev_minutes` only when at least three sessions exist.

Run: `python -m pytest tests/test_analytics.py -v`

Expected: all analytics tests pass, including insufficient samples and zero baseline.

- [ ] **Step 5: Commit**

```text
git add src/fitbit_health/analytics.py tests/test_analytics.py
git commit -m "feat: calculate sample-aware health trends"
```

---

### Task 6: Private JSON Outputs and Chinese Markdown Report

**Files:**
- Create: `src/fitbit_health/report.py`
- Create: `tests/test_report.py`

**Interfaces:**
- Consumes: normalized document, analysis document, and output directory.
- Produces: `write_outputs(normalized: dict, analysis: dict, output_dir: Path) -> tuple[Path, Path, Path]`.

- [ ] **Step 1: Write failing output and wording tests**

```python
# tests/test_report.py
import json
from pathlib import Path

from fitbit_health.report import render_markdown, write_outputs


def test_report_includes_samples_missing_data_and_disclaimer() -> None:
    analysis = {"metrics": {"steps": {"current_mean": 8000, "current_samples": 5,
        "baseline_mean": 7000, "baseline_samples": 18, "absolute_change": 1000, "percent_change": 14.29}},
        "data_quality": {"days_requested": 30, "days_with_sleep": 20, "diagnostics": {"hrv": "HTTP 403"}}}
    text = render_markdown(analysis)
    assert "有效样本 5 天" in text
    assert "HRV" in text or "hrv" in text
    assert "不构成医疗诊断" in text
    assert "恢复评分" not in text


def test_write_outputs_uses_utf8_json(tmp_path: Path) -> None:
    paths = write_outputs({"schema_version": 1, "days": []}, {"metrics": {}, "data_quality": {}}, tmp_path)
    assert [path.name for path in paths] == ["daily_health_summary.json", "health_analysis.json", "health_report.md"]
    assert json.loads(paths[0].read_text(encoding="utf-8"))["schema_version"] == 1
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_report.py -v`

Expected: import fails because `fitbit_health.report` does not exist.

- [ ] **Step 3: Implement deterministic rendering and writes**

```python
# src/fitbit_health/report.py
import json
from pathlib import Path

LABELS = {
    "sleep_minutes": "睡眠时长（分钟）", "steps": "步数", "heart_rate_average": "平均心率",
    "resting_heart_rate": "静息心率", "hrv_rmssd": "HRV（RMSSD）",
}


def render_markdown(analysis: dict) -> str:
    lines = ["# Fitbit Health 趋势报告", "", "## 最近 7 天与基线"]
    for name, metric in analysis.get("metrics", {}).items():
        label = LABELS.get(name, name)
        if metric.get("current_mean") is None:
            lines.append(f"- {label}：无有效数据。")
            continue
        change = metric.get("percent_change")
        change_text = "样本不足，暂不判断趋势" if change is None else f"较基线变化 {change:+.2f}%"
        lines.append(f"- {label}：{metric['current_mean']}（有效样本 {metric['current_samples']} 天；{change_text}）。")
    quality = analysis.get("data_quality", {})
    lines.extend(["", "## 数据质量", f"- 请求天数：{quality.get('days_requested', 0)}",
                  f"- 有睡眠记录：{quality.get('days_with_sleep', 0)} 天"])
    for name, error in quality.get("diagnostics", {}).items():
        lines.append(f"- {name}：{error}")
    lines.extend(["", "> 本报告仅描述可穿戴设备数据趋势，不构成医疗诊断、治疗或用药建议。", ""])
    return "\n".join(lines)


def write_outputs(normalized: dict, analysis: dict, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = output_dir / "daily_health_summary.json"
    metrics = output_dir / "health_analysis.json"
    report = output_dir / "health_report.md"
    summary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    report.write_text(render_markdown(analysis), encoding="utf-8")
    return summary, metrics, report
```

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_report.py -v`

Expected: all report tests pass and outputs decode as UTF-8.

- [ ] **Step 5: Commit**

```text
git add src/fitbit_health/report.py tests/test_report.py
git commit -m "feat: render private Chinese health reports"
```

---

### Task 7: End-to-End Pipeline, CLI, and Synthetic Verification

**Files:**
- Create: `src/fitbit_health/pipeline.py`
- Create: `src/fitbit_health/__main__.py`
- Create: `tests/test_pipeline.py`
- Create: `README.md`

**Interfaces:**
- Consumes: workspace root, day count, optional current date for tests.
- Produces: `run_sync(root: Path, days: int, today: date | None = None) -> tuple[Path, Path, Path]` and CLI `python -m fitbit_health sync --days 30`.

- [ ] **Step 1: Write a failing synthetic end-to-end test**

```python
# tests/test_pipeline.py
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from fitbit_health.client import FetchResult
from fitbit_health.pipeline import run_sync


def test_pipeline_writes_all_outputs_without_real_api(tmp_path: Path) -> None:
    fake_client = Mock()
    fake_client.fetch_all.side_effect = lambda data_type, start: FetchResult(data_type, [])
    paths = run_sync(tmp_path, days=30, today=date(2026, 7, 18), client=fake_client)
    assert all(path.exists() for path in paths)
    assert fake_client.fetch_all.call_count == 5
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_pipeline.py -v`

Expected: import fails because `fitbit_health.pipeline` does not exist.

- [ ] **Step 3: Implement pipeline composition with injectable client**

```python
# src/fitbit_health/pipeline.py
from datetime import date, timedelta
from pathlib import Path

from fitbit_health.analytics import analyze
from fitbit_health.auth import load_credentials
from fitbit_health.client import GoogleHealthClient
from fitbit_health.config import SCOPES, find_installed_credentials
from fitbit_health.normalize import normalize_results
from fitbit_health.report import write_outputs

DATA_TYPES = ("sleep", "steps", "heart-rate", "daily-resting-heart-rate", "daily-heart-rate-variability")


def run_sync(root: Path, days: int, today: date | None = None, client=None):
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    if client is None:
        client_path = find_installed_credentials(root)
        credentials = load_credentials(client_path, root / ".private" / "token.json", SCOPES)
        client = GoogleHealthClient(credentials)
    results = {name: client.fetch_all(name, start) for name in DATA_TYPES}
    normalized = normalize_results(results, start, end)
    analysis = analyze(normalized)
    return write_outputs(normalized, analysis, root / "reports")
```

- [ ] **Step 4: Add CLI failure tests, then implement argparse entry point**

```python
# src/fitbit_health/__main__.py
import argparse
from pathlib import Path
import sys

from fitbit_health.auth import AuthError
from fitbit_health.config import ConfigError
from fitbit_health.pipeline import run_sync


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fitbit-health")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync = subparsers.add_parser("sync")
    sync.add_argument("--days", type=int, default=30, choices=range(1, 366), metavar="1..365")
    args = parser.parse_args(argv)
    try:
        paths = run_sync(Path.cwd(), args.days)
    except (ConfigError, AuthError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run: `python -m pytest tests/test_pipeline.py -v`

Expected: pipeline and CLI tests pass without opening a browser.

- [ ] **Step 5: Write operator documentation**

README must contain exact commands:

```text
python -m pip install -e ".[test]"
python -m fitbit_health sync --days 30
python -m pytest -q
```

It must explain that the first sync opens Google OAuth, localhost only responds while the command is running, outputs are private and Git-ignored, testing-mode refresh tokens may expire, and MCP is deferred.

- [ ] **Step 6: Run full synthetic verification**

Run: `python -m pytest -q`

Expected: all tests pass with zero failures.

Run: `python -m compileall -q src tests`

Expected: exit code `0` and no output.

Run: `git status --short --ignored`

Expected: OAuth JSON files appear with `!!`; no credential or health-data file is staged.

- [ ] **Step 7: Commit**

```text
git add src/fitbit_health/pipeline.py src/fitbit_health/__main__.py tests/test_pipeline.py README.md
git commit -m "feat: add Fitbit Health sync CLI"
```

---

### Task 8: One-Time Live Read-Only API Verification

**Files:**
- Modify: `README.md`
- Generated but ignored: `.private/token.json`
- Generated but ignored: `reports/daily_health_summary.json`
- Generated but ignored: `reports/health_analysis.json`
- Generated but ignored: `reports/health_report.md`

**Interfaces:**
- Consumes: the downloaded desktop OAuth credential and the user's interactive consent.
- Produces: a verified local report and a sanitized verification summary; no source API response is committed.

- [ ] **Step 1: Confirm privacy boundary before authorization**

Run: `git check-ignore -v client_secret_*.json`

Expected: every OAuth credential matches `.gitignore`.

- [ ] **Step 2: Run the live sync**

Run: `python -m fitbit_health sync --days 30`

Expected: browser opens once, the user grants the three read-only scopes, localhost briefly reports success, and three report paths are printed. If Google reports `access_denied`, verify the signed-in account is listed under OAuth test users and the scopes are enabled in Data Access.

- [ ] **Step 3: Validate outputs without printing private measurements**

Run: `python -m pytest -q`

Expected: all tests still pass.

Run a schema-only command that prints only keys, date range, day count, and diagnostic names; it must not print metric values or token fields.

Expected: schema version `1`, 30 daily records, and only known diagnostic names.

- [ ] **Step 4: Scan tracked files for sensitive artifacts**

Run: `git status --short --ignored`

Expected: `.private/`, `reports/`, and both `client_secret_*.json` files are ignored (`!!`).

Run: `git grep -n -E "refresh_token|access_token|client_secret" -- ':!docs/superpowers/**'`

Expected: no real token or secret values in tracked files; identifier names in source are acceptable only where required by OAuth libraries.

- [ ] **Step 5: Record the live-check procedure, not personal results**

Update README troubleshooting with any OAuth or endpoint issue encountered. Do not record health measurements, account identifiers, tokens, or raw response bodies.

- [ ] **Step 6: Final verification and commit**

Run: `python -m pytest -q`

Expected: all tests pass with zero failures.

Run: `python -m compileall -q src tests`

Expected: exit code `0`.

```text
git add README.md
git commit -m "docs: record live Google Health verification"
```

