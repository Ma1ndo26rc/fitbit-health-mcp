import json
from pathlib import Path


LABELS = {
    "sleep_minutes": "睡眠时长（分钟）",
    "steps": "步数",
    "heart_rate_average": "平均心率",
    "resting_heart_rate": "静息心率",
    "hrv_rmssd": "HRV（RMSSD）",
}


def _format_number(value: float | int | None) -> str:
    if value is None:
        return "—"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def render_markdown(analysis: dict) -> str:
    """Render deterministic Chinese trend commentary without diagnosis."""
    lines = [
        "# Fitbit Health 趋势报告",
        "",
        "## 最近 7 天与基线",
    ]
    for name, metric in analysis.get("metrics", {}).items():
        label = LABELS.get(name, name)
        current = metric.get("current_mean")
        if current is None:
            lines.append(f"- {label}：无有效数据。")
            continue
        percent = metric.get("percent_change")
        if percent is None:
            trend = "样本不足或基线为零，暂不判断趋势"
        else:
            trend = f"较前序基线变化 {percent:+.2f}%"
        lines.append(
            f"- {label}：{_format_number(current)}"
            f"（有效样本 {metric.get('current_samples', 0)} 天；{trend}；"
            f"30 天均值 {_format_number(metric.get('thirty_day_mean'))}，"
            f"样本 {metric.get('thirty_day_samples', 0)} 天）。"
        )

    regularity = analysis.get("sleep_regularity", {})
    lines.extend(["", "## 睡眠规律性"])
    if regularity.get("samples", 0) < 3:
        lines.append("- 睡眠会话样本不足，暂不评估作息规律性。")
    else:
        lines.append(
            f"- 入睡时间波动：{_format_number(regularity.get('sleep_start_stddev_minutes'))} 分钟。"
        )
        lines.append(
            f"- 起床时间波动：{_format_number(regularity.get('wake_time_stddev_minutes'))} 分钟。"
        )
        lines.append(f"- 有效睡眠会话：{regularity.get('samples')} 个。")

    quality = analysis.get("data_quality", {})
    lines.extend(
        [
            "",
            "## 数据质量",
            f"- 请求天数：{quality.get('days_requested', 0)} 天。",
            f"- 有睡眠记录：{quality.get('days_with_sleep', 0)} 天。",
        ]
    )
    diagnostics = quality.get("diagnostics", {})
    if diagnostics:
        for name, error in diagnostics.items():
            lines.append(f"- {name}：{error}")
    else:
        lines.append("- API 未报告数据类型错误。")

    lines.extend(
        [
            "",
            "> 本报告仅描述可穿戴设备数据趋势，不构成医疗诊断、治疗或用药建议。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    normalized: dict,
    analysis: dict,
    output_dir: Path,
) -> tuple[Path, Path, Path]:
    """Write private, UTF-8 JSON and Markdown outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "daily_health_summary.json"
    analysis_path = output_dir / "health_analysis.json"
    report_path = output_dir / "health_report.md"

    summary_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    analysis_path.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path.write_text(render_markdown(analysis), encoding="utf-8")
    return summary_path, analysis_path, report_path
