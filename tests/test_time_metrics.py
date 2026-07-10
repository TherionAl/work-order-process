from datetime import datetime

from work_order_process.business_time import WorkCalendar
from work_order_process.time_metrics import TimeMetricDefinition, _compute_metric_row


def _ticket() -> dict:
    return {
        "ticket_id": 1,
        "create_dt": "2026-06-16 10:00:00",
        "create_month_label": "2026-06",
        "ticket_template_id": "30",
        "subject": "test",
    }


def _metric() -> TimeMetricDefinition:
    return TimeMetricDefinition(
        code="approval_duration",
        name="审批时长",
        start_field="field_249",
        end_field="field_331",
    )


def test_compute_metric_row_success() -> None:
    row = _compute_metric_row(
        _ticket(),
        _metric(),
        {
            "field_249": "2026-06-16 10:23:55",
            "field_331": "2026-06-18 03:21:18",
        },
        WorkCalendar(overrides={}, names={}),
    )

    assert row["status"] == "success"
    assert row["raw_minutes"] == 2457
    assert row["business_minutes"] == 776


def test_compute_metric_row_missing_end() -> None:
    row = _compute_metric_row(
        _ticket(),
        _metric(),
        {"field_249": "2026-06-16 10:23:55"},
        WorkCalendar(overrides={}, names={}),
    )

    assert row["status"] == "missing_end"
    assert row["business_minutes"] is None


def test_compute_metric_row_invalid_time_order() -> None:
    row = _compute_metric_row(
        _ticket(),
        _metric(),
        {
            "field_249": "2026-06-18 03:21:18",
            "field_331": "2026-06-16 10:23:55",
        },
        WorkCalendar(overrides={}, names={}),
    )

    assert row["status"] == "invalid_time_order"
    assert row["business_minutes"] is None
