"""Deterministic orchestration for scheduled Hubei quality inspections."""

from __future__ import annotations

import subprocess
import sys
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import PROJECT_ROOT
from .hubei_analysis import DEFAULT_PROVINCE

WEEKLY_RUN_DAYS = frozenset({8, 15, 22, 29})
SCHEDULE_HOUR = 5
SCHEDULE_MINUTE = 17


@dataclass(frozen=True)
class QualityWindow:
    period: str
    start: datetime
    end: datetime
    overwrite_existing: bool


def scheduled_window(period: str, run_at: datetime) -> QualityWindow:
    """Return the exact reporting window for one scheduled execution date."""

    end = run_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        if run_at.day not in WEEKLY_RUN_DAYS:
            raise ValueError(f"{run_at:%Y-%m-%d} 不是湖北周检执行日")
        return QualityWindow(
            period=period,
            start=end - timedelta(days=7),
            end=end,
            overwrite_existing=False,
        )
    if period == "monthly":
        if run_at.day != 1:
            raise ValueError(f"{run_at:%Y-%m-%d} 不是湖北月结执行日")
        previous_month = (end - timedelta(days=1)).replace(day=1)
        return QualityWindow(
            period=period,
            start=previous_month,
            end=end,
            overwrite_existing=True,
        )
    raise ValueError(f"不支持的湖北质检周期: {period}")


def most_recent_scheduled_at(period: str, run_at: datetime) -> datetime:
    """Return the latest nominal execution time at or before ``run_at``."""

    month_start = run_at.replace(
        day=1,
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        second=0,
        microsecond=0,
    )
    if period == "monthly":
        if month_start <= run_at:
            return month_start
        return (month_start - timedelta(days=1)).replace(day=1)
    if period != "weekly":
        raise ValueError(f"不支持的湖北质检周期: {period}")

    current_candidates = [
        month_start.replace(day=day)
        for day in WEEKLY_RUN_DAYS
        if day <= monthrange(month_start.year, month_start.month)[1]
        and month_start.replace(day=day) <= run_at
    ]
    if current_candidates:
        return max(current_candidates)

    previous_month_last_day = month_start - timedelta(days=1)
    previous_candidates = [day for day in WEEKLY_RUN_DAYS if day <= previous_month_last_day.day]
    return previous_month_last_day.replace(
        day=max(previous_candidates),
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        second=0,
        microsecond=0,
    )


def run_scheduled_quality(
    period: str,
    *,
    run_at: datetime | None = None,
    apply: bool = False,
    allow_catch_up: bool = False,
    project_root: Path = PROJECT_ROOT,
    python_executable: str = sys.executable,
    run_command: Callable[..., object] | None = None,
) -> Path:
    """Generate a scoped report, then validate or apply its API writeback."""

    actual_run_at = run_at or datetime.now()
    nominal_run_at = (
        most_recent_scheduled_at(period, actual_run_at) if allow_catch_up else actual_run_at
    )
    window = scheduled_window(period, nominal_run_at)
    output_dir = project_root / "output" / "compliance_check"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / (
        "故障单合规性检查_湖北_"
        f"{period}_{window.start:%Y%m%d}_{window.end:%Y%m%d}_"
        f"{actual_run_at:%Y%m%d_%H%M%S}.xlsx"
    )
    execute = run_command or subprocess.run

    report_command = [
        python_executable,
        str(project_root / "scripts" / "compliance_check.py"),
        "--start",
        window.start.isoformat(sep=" "),
        "--end",
        window.end.isoformat(sep=" "),
        "--province",
        DEFAULT_PROVINCE,
        "--quality-period",
        period,
        "--output-file",
        str(report_path),
    ]
    execute(report_command, cwd=project_root, check=True)

    writeback_command = [
        python_executable,
        str(project_root / "scripts" / "apply_hubei_sampling_status.py"),
        "--input",
        str(report_path),
    ]
    if apply:
        writeback_command.append("--apply")
    if window.overwrite_existing:
        writeback_command.append("--overwrite-existing")
    execute(writeback_command, cwd=project_root, check=True)
    return report_path
