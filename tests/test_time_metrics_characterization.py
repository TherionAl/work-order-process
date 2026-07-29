from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from work_order_process import time_metrics
from work_order_process.business_time import WorkCalendar
from work_order_process.config import MySQLConfig
from work_order_process.time_metrics import (
    TimeMetricDefinition,
    _compute_metric_row,
    _fetch_metric_field_values,
    _fetch_month_tickets,
    _fetch_ticket,
    export_month_time_metrics,
    export_ticket_time_metrics,
    load_metric_definitions,
)


def _mysql() -> MySQLConfig:
    return MySQLConfig("db.local", 3307, "reader", "secret", "work_order")


def _ticket(ticket_id: int = 9) -> dict:
    return {
        "ticket_id": ticket_id,
        "create_dt": "2026-07-01 09:00:00",
        "create_month_label": "2026-07",
        "ticket_template_id": "TP1",
        "subject": "安装",
        "source_updated_at": "2026-07-01 12:00:00",
    }


def _metric() -> TimeMetricDefinition:
    return TimeMetricDefinition(
        code="approval",
        name="审批时长",
        start_field="field_start",
        end_field="field_end",
    )


@pytest.mark.parametrize(
    ("fields", "status", "message"),
    [
        ({}, "missing_both", "start and end field values are empty"),
        ({"field_end": "2026-07-01 10:00:00"}, "missing_start", "field_start is empty"),
        ({"field_start": "2026-07-01 09:00:00"}, "missing_end", "field_end is empty"),
        (
            {"field_start": "invalid", "field_end": "2026-07-01 10:00:00"},
            "invalid_start",
            "cannot parse field_start: invalid",
        ),
        (
            {"field_start": "2026-07-01 09:00:00", "field_end": "invalid"},
            "invalid_end",
            "cannot parse field_end: invalid",
        ),
        (
            {"field_start": "2026-07-01 11:00:00", "field_end": "2026-07-01 10:00:00"},
            "invalid_time_order",
            "end time is earlier than start time",
        ),
    ],
)
def test_metric_validation_reports_missing_invalid_and_reversed_times(
    fields: dict[str, str], status: str, message: str
) -> None:
    row = _compute_metric_row(
        _ticket(),
        _metric(),
        fields,
        WorkCalendar(overrides={}, names={}),
    )

    assert row["ticket_id"] == 9
    assert row["metric_code"] == "approval"
    assert row["status"] == status
    assert row["error_message"] == message
    assert row["raw_minutes"] is None
    assert row["business_minutes"] is None


class _Cursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, object]] = []
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params: object) -> None:
        self._sql = sql
        self.executions.append((sql, params))

    def fetchall(self):
        if "ticket_detail_custom_fields" in self._sql:
            return [
                (9, "field_start", "2026-07-01 09:00:00"),
                (9, "field_start", "ignored duplicate"),
                (9, "field_end", "2026-07-01 10:00:00"),
            ]
        return [
            (
                9,
                datetime(2026, 7, 1, 9, 0),
                "2026-07",
                "TP1",
                "安装",
                datetime(2026, 7, 1, 12, 0),
            )
        ]

    def fetchone(self):
        return (
            9,
            datetime(2026, 7, 1, 9, 0),
            "2026-07",
            "TP1",
            "安装",
            datetime(2026, 7, 1, 12, 0),
        )


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


def test_month_single_ticket_and_custom_field_queries_are_bounded_and_mapped(
    monkeypatch,
) -> None:
    cursor = _Cursor()
    connection = _Connection(cursor)
    connects: list[dict] = []

    def connect(**kwargs):
        connects.append(kwargs)
        return connection

    monkeypatch.setattr(
        time_metrics,
        "_pymysql",
        lambda: SimpleNamespace(connect=connect),
    )

    month_rows = _fetch_month_tickets(_mysql(), "2026-07", limit=5)
    ticket = _fetch_ticket(_mysql(), "9")
    fields = _fetch_metric_field_values(_mysql(), "2026-07", [9], [_metric()])

    assert month_rows == [_ticket()]
    assert ticket == _ticket()
    assert fields == {
        9: {
            "field_start": "2026-07-01 09:00:00",
            "field_end": "2026-07-01 10:00:00",
        }
    }
    month_sql, month_params = cursor.executions[0]
    assert "WHERE create_month_label = %s" in month_sql
    assert "ORDER BY create_dt, ticket_id LIMIT %s" in month_sql
    assert month_params == ["2026-07", 5]
    ticket_sql, ticket_params = cursor.executions[1]
    assert "WHERE ticket_id = %s ORDER BY create_dt DESC LIMIT 1" in ticket_sql
    assert ticket_params == ("9",)
    field_sql, field_params = cursor.executions[2]
    assert "WHERE create_month_label = %s AND ticket_id IN (%s)" in field_sql
    assert "AND field_key IN (%s, %s)" in field_sql
    assert field_params == ["2026-07", 9, "field_end", "field_start"]
    assert (
        connects
        == [
            {
                "host": "db.local",
                "port": 3307,
                "user": "reader",
                "password": "secret",
                "database": "work_order",
                "charset": "utf8mb4",
            }
        ]
        * 3
    )


def _write_configs(tmp_path: Path) -> tuple[Path, Path]:
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "metrics": [
                    {
                        "code": "approval",
                        "name": "审批时长",
                        "start_field": "field_start",
                        "end_field": "field_end",
                    },
                    {
                        "code": "disabled",
                        "name": "禁用指标",
                        "start_field": "x",
                        "end_field": "y",
                        "enabled": False,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(
        json.dumps(
            {
                "work_sessions": ["09:00-12:00"],
                "days": {"2026-07-01": {"is_workday": True, "name": "工作日"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return metrics_path, calendar_path


def test_metric_code_filtering_and_unknown_metric_error(tmp_path: Path) -> None:
    metrics_path, _ = _write_configs(tmp_path)

    metrics = load_metric_definitions(metrics_path, metric_code="approval")

    assert metrics == [_metric()]
    with pytest.raises(ValueError, match="Metric not found or disabled: unknown"):
        load_metric_definitions(metrics_path, metric_code="unknown")


def test_month_export_writes_successful_json_report(monkeypatch, tmp_path: Path) -> None:
    metrics_path, calendar_path = _write_configs(tmp_path)
    query_calls: list[object] = []
    monkeypatch.setattr(
        time_metrics,
        "_fetch_month_tickets",
        lambda mysql, month, limit: (
            query_calls.append(("tickets", mysql, month, limit)) or [_ticket()]
        ),
    )
    monkeypatch.setattr(
        time_metrics,
        "_fetch_metric_field_values",
        lambda mysql, month, ids, metrics: (
            query_calls.append(("fields", mysql, month, ids, [item.code for item in metrics]))
            or {
                9: {
                    "field_start": "2026-07-01 09:15:00",
                    "field_end": "2026-07-01 10:45:00",
                }
            }
        ),
    )
    output = tmp_path / "exports" / "approval.json"

    report = export_month_time_metrics(
        _mysql(),
        year=2026,
        month=7,
        output_dir=tmp_path,
        metrics_config_path=metrics_path,
        calendar_path=calendar_path,
        metric_code="approval",
        limit=10,
        output_path=output,
    )

    assert query_calls == [
        ("tickets", _mysql(), "2026-07", 10),
        ("fields", _mysql(), "2026-07", [9], ["approval"]),
    ]
    assert report["month"] == "2026-07"
    assert report["ticket_count"] == 1
    assert report["summary"] == {"status_counts": {"success": 1}}
    assert report["rows"][0]["raw_minutes"] == 90
    assert report["rows"][0]["business_minutes"] == 90
    assert report["output_path"] == str(output)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["rows"][0]["metric_code"] == "approval"
    assert written["rows"][0]["start_time"] == "2026-07-01 09:15:00"
    assert "output_path" not in written


def test_single_ticket_export_queries_month_fields_and_reports_missing_ticket(
    monkeypatch, tmp_path: Path
) -> None:
    metrics_path, calendar_path = _write_configs(tmp_path)
    monkeypatch.setattr(time_metrics, "_fetch_ticket", lambda mysql, ticket_id: _ticket())
    calls: list[tuple[str, list[int]]] = []
    monkeypatch.setattr(
        time_metrics,
        "_fetch_metric_field_values",
        lambda mysql, month, ids, metrics: (
            calls.append((month, ids))
            or {
                9: {
                    "field_start": "2026-07-01 09:00:00",
                    "field_end": "2026-07-01 09:30:00",
                }
            }
        ),
    )

    report = export_ticket_time_metrics(
        _mysql(),
        ticket_id="9",
        output_dir=tmp_path,
        metrics_config_path=metrics_path,
        calendar_path=calendar_path,
    )

    assert calls == [("2026-07", [9])]
    assert report["ticket_id"] == "9"
    assert report["rows"][0]["business_minutes"] == 30
    assert Path(report["output_path"]).name == "ticket_9_time_metrics.json"

    monkeypatch.setattr(time_metrics, "_fetch_ticket", lambda mysql, ticket_id: None)
    with pytest.raises(ValueError, match="Ticket not found in MySQL: missing"):
        export_ticket_time_metrics(
            _mysql(),
            ticket_id="missing",
            output_dir=tmp_path,
            metrics_config_path=metrics_path,
            calendar_path=calendar_path,
        )
