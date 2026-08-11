"""按生产调度窗口生成湖北质检报告并执行可审计回写。"""

from __future__ import annotations

import argparse
from datetime import datetime

from work_order_process.hubei_quality_schedule import run_scheduled_quality


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=("weekly", "monthly"), required=True)
    parser.add_argument(
        "--run-at",
        type=datetime.fromisoformat,
        help="可选的 ISO-8601 执行时间，用于受控补跑；默认使用当前时间",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际回写接口；未指定时生成报告并预演回写",
    )
    parser.add_argument(
        "--allow-catch-up",
        action="store_true",
        help="错过计划时刻时使用最近一次计划窗口；供 Persistent timer 使用",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report_path = run_scheduled_quality(
        args.period,
        run_at=args.run_at,
        apply=args.apply,
        allow_catch_up=args.allow_catch_up,
    )
    print(f"湖北{args.period}质检完成，报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
