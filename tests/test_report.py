import json
from pathlib import Path

from fitbit_health.report import render_markdown, write_outputs


def example_analysis() -> dict:
    return {
        "schema_version": 1,
        "metrics": {
            "steps": {
                "current_mean": 8000.0,
                "current_samples": 5,
                "thirty_day_mean": 7200.0,
                "thirty_day_samples": 23,
                "baseline_mean": 7000.0,
                "baseline_samples": 18,
                "absolute_change": 1000.0,
                "percent_change": 14.29,
            },
            "hrv_rmssd": {
                "current_mean": None,
                "current_samples": 0,
                "thirty_day_mean": None,
                "thirty_day_samples": 0,
                "baseline_mean": None,
                "baseline_samples": 0,
                "absolute_change": None,
                "percent_change": None,
            },
        },
        "sleep_regularity": {
            "samples": 6,
            "sleep_start_stddev_minutes": 22.5,
            "wake_time_stddev_minutes": 18.0,
        },
        "data_quality": {
            "days_requested": 30,
            "days_with_sleep": 20,
            "diagnostics": {"daily-heart-rate-variability": "HTTP 403: permission denied"},
        },
    }


def test_report_includes_samples_trends_missing_data_and_disclaimer() -> None:
    text = render_markdown(example_analysis())

    assert "有效样本 5 天" in text
    assert "+14.29%" in text
    assert "HRV（RMSSD）：无有效数据" in text
    assert "入睡时间波动：22.5 分钟" in text
    assert "daily-heart-rate-variability：HTTP 403: permission denied" in text
    assert "不构成医疗诊断" in text
    assert "恢复评分" not in text


def test_write_outputs_uses_utf8_and_stable_names(tmp_path: Path) -> None:
    normalized = {"schema_version": 1, "days": []}
    analysis = example_analysis()

    paths = write_outputs(normalized, analysis, tmp_path)

    assert [path.name for path in paths] == [
        "daily_health_summary.json",
        "health_analysis.json",
        "health_report.md",
    ]
    assert json.loads(paths[0].read_text(encoding="utf-8"))["schema_version"] == 1
    assert paths[2].read_text(encoding="utf-8").startswith("# Fitbit Health 趋势报告")
