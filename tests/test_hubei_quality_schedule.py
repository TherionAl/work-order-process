from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from work_order_process.hubei_quality_schedule import (
    run_scheduled_quality,
    scheduled_window,
)


def test_weekly_window_uses_the_previous_complete_seven_day_segment() -> None:
    window = scheduled_window("weekly", datetime(2026, 8, 8, 5, 17))

    assert window.start == datetime(2026, 8, 1)
    assert window.end == datetime(2026, 8, 8)
    assert window.overwrite_existing is False


def test_monthly_window_uses_the_previous_complete_month_across_years() -> None:
    window = scheduled_window("monthly", datetime(2027, 1, 1, 5, 17))

    assert window.start == datetime(2026, 12, 1)
    assert window.end == datetime(2027, 1, 1)
    assert window.overwrite_existing is True


@pytest.mark.parametrize(
    ("period", "run_at"),
    [
        ("weekly", datetime(2026, 8, 7, 5, 17)),
        ("monthly", datetime(2026, 8, 2, 5, 17)),
    ],
)
def test_window_rejects_dates_not_scheduled_for_the_period(period: str, run_at: datetime) -> None:
    with pytest.raises(ValueError, match="不是.*执行日"):
        scheduled_window(period, run_at)


def test_monthly_run_passes_exact_scope_and_overwrite_to_existing_clis(tmp_path: Path) -> None:
    commands: list[tuple[list[str], Path, bool]] = []

    def record(command: list[str], *, cwd: Path, check: bool) -> None:
        commands.append((command, cwd, check))

    report_path = run_scheduled_quality(
        "monthly",
        run_at=datetime(2026, 8, 1, 5, 17),
        apply=True,
        project_root=tmp_path,
        python_executable="python-test",
        run_command=record,
    )

    assert report_path == (
        tmp_path
        / "output"
        / "compliance_check"
        / "故障单合规性检查_湖北_monthly_20260701_20260801_20260801_051700.xlsx"
    )
    assert commands == [
        (
            [
                "python-test",
                str(tmp_path / "scripts" / "compliance_check.py"),
                "--start",
                "2026-07-01 00:00:00",
                "--end",
                "2026-08-01 00:00:00",
                "--province",
                "湖北省",
                "--quality-period",
                "monthly",
                "--output-file",
                str(report_path),
            ],
            tmp_path,
            True,
        ),
        (
            [
                "python-test",
                str(tmp_path / "scripts" / "apply_hubei_sampling_status.py"),
                "--input",
                str(report_path),
                "--apply",
                "--overwrite-existing",
            ],
            tmp_path,
            True,
        ),
    ]


def test_weekly_dry_run_does_not_request_apply_or_overwrite(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def record(command: list[str], *, cwd: Path, check: bool) -> None:
        commands.append(command)

    run_scheduled_quality(
        "weekly",
        run_at=datetime(2026, 8, 15, 5, 17),
        apply=False,
        project_root=tmp_path,
        python_executable="python-test",
        run_command=record,
    )

    assert "--apply" not in commands[1]
    assert "--overwrite-existing" not in commands[1]


def test_monthly_catch_up_uses_the_most_recent_first_day_window(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def record(command: list[str], *, cwd: Path, check: bool) -> None:
        commands.append(command)

    run_scheduled_quality(
        "monthly",
        run_at=datetime(2026, 8, 3, 9, 0),
        allow_catch_up=True,
        project_root=tmp_path,
        python_executable="python-test",
        run_command=record,
    )

    assert commands[0][commands[0].index("--start") + 1] == "2026-07-01 00:00:00"
    assert commands[0][commands[0].index("--end") + 1] == "2026-08-01 00:00:00"
