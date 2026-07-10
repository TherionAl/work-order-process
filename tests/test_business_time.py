from datetime import datetime

from work_order_process.business_time import WorkCalendar, business_minutes_between


def test_business_minutes_between_cross_days_excludes_non_work_time() -> None:
    calendar = WorkCalendar(overrides={}, names={})

    minutes = business_minutes_between(
        datetime(2026, 6, 16, 10, 23, 55),
        datetime(2026, 6, 18, 3, 21, 18),
        calendar,
    )

    assert minutes == 776


def test_business_minutes_between_excludes_holiday() -> None:
    calendar = WorkCalendar(overrides={datetime(2026, 6, 17).date(): False}, names={})

    minutes = business_minutes_between(
        datetime(2026, 6, 16, 10, 0, 0),
        datetime(2026, 6, 18, 10, 0, 0),
        calendar,
    )

    assert minutes == 430


def test_business_minutes_between_counts_makeup_workday() -> None:
    calendar = WorkCalendar(overrides={datetime(2026, 1, 4).date(): True}, names={})

    minutes = business_minutes_between(
        datetime(2026, 1, 4, 9, 0, 0),
        datetime(2026, 1, 4, 18, 0, 0),
        calendar,
    )

    assert minutes == 430
