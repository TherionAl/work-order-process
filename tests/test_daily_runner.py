from __future__ import annotations

from datetime import datetime

import pytest

from work_order_process import daily_runner


def test_maintenance_months_cover_old_previous_year_months_in_january() -> None:
    assert daily_runner.maintenance_months(datetime(2027, 1, 1)) == [
        (2026, month) for month in range(1, 10)
    ]


def test_maintenance_months_cover_old_current_year_months_in_july() -> None:
    assert daily_runner.maintenance_months(datetime(2026, 7, 24)) == [
        (2026, 1),
        (2026, 2),
        (2026, 3),
    ]


def test_daily_job_processes_all_months_then_raises_for_failed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 24)

    reports = iter(
        [
            {"failed": 0},
            {"failed": 2, "failed_ids": ["T1", "T2"]},
            {"failed": 0},
            {"failed": 0},
        ]
    )
    calls: list[tuple[int, int]] = []

    def fake_sync(year: int, month: int):
        calls.append((year, month))
        return next(reports)

    monkeypatch.setattr(daily_runner, "datetime", FixedDatetime)
    monkeypatch.setattr(daily_runner, "sync_tickets_for_month", fake_sync)

    with pytest.raises(daily_runner.ScheduledSyncError, match=r"2026-06.*failed=2"):
        daily_runner.job_sync_tickets_daily()

    assert calls == [
        (2026, 7),
        (2026, 6),
        (2026, 5),
        (2026, 4),
    ]


def test_daily_job_processes_remaining_months_after_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 24)

    calls: list[tuple[int, int]] = []

    def fake_sync(year: int, month: int):
        calls.append((year, month))
        if month == 6:
            raise RuntimeError("API unavailable")
        return {"failed": 0}

    monkeypatch.setattr(daily_runner, "datetime", FixedDatetime)
    monkeypatch.setattr(daily_runner, "sync_tickets_for_month", fake_sync)

    with pytest.raises(daily_runner.ScheduledSyncError, match="API unavailable"):
        daily_runner.job_sync_tickets_daily()

    assert len(calls) == 4
