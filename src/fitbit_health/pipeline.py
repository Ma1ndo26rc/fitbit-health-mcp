from datetime import date, timedelta
from pathlib import Path

from fitbit_health.analytics import analyze
from fitbit_health.auth import load_credentials
from fitbit_health.client import FetchResult, GoogleHealthClient
from fitbit_health.config import SCOPES, find_installed_credentials
from fitbit_health.fetch_window import FETCH_DAYS_ERROR, is_allowed_fetch_days
from fitbit_health.normalize import normalize_results
from fitbit_health.report import write_outputs


DATA_TYPES = (
    "sleep",
    "steps",
    "heart-rate",
    "daily-resting-heart-rate",
    "daily-heart-rate-variability",
)


class PipelineError(RuntimeError):
    """Raised when a sync cannot produce a meaningful local result."""


def run_sync(
    root: Path,
    days: int,
    today: date | None = None,
    client: GoogleHealthClient | None = None,
) -> tuple[Path, Path, Path]:
    """Fetch, normalize, analyze, and write a local health report."""
    if not is_allowed_fetch_days(days):
        raise ValueError(FETCH_DAYS_ERROR)

    end_date = today or date.today()
    start_date = end_date - timedelta(days=days - 1)
    if client is None:
        client_path = find_installed_credentials(root)
        credentials = load_credentials(
            client_path,
            root / ".private" / "token.json",
            SCOPES,
        )
        client = GoogleHealthClient(credentials)

    results: dict[str, FetchResult] = {
        data_type: client.fetch_all(data_type, start_date)
        for data_type in DATA_TYPES
    }
    if all(result.error is not None for result in results.values()):
        raise PipelineError("全部 Google Health 数据请求均失败，未生成报告。")

    normalized = normalize_results(results, start_date, end_date)
    analysis = analyze(normalized)
    return write_outputs(normalized, analysis, root / "reports")
