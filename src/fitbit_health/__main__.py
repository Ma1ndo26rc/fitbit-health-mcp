import argparse
from pathlib import Path
import sys

from fitbit_health.auth import AuthError
from fitbit_health.config import ConfigError
from fitbit_health.pipeline import PipelineError, run_sync


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitbit-health",
        description="在本地同步和分析 Google Health / Fitbit Air 数据。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="同步健康数据并生成本地报告")
    sync.add_argument(
        "--days",
        type=int,
        default=30,
        choices=range(1, 366),
        metavar="1..365",
        help="同步天数，默认 30",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = run_sync(Path.cwd(), args.days)
    except (ConfigError, AuthError, PipelineError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
