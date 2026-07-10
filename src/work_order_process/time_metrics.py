"""Config-driven ticket time metrics exported as JSON."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .business_time import WorkCalendar, business_minutes_between
from .config import MySQLConfig, PROJECT_ROOT
from .io import write_json


DEFAULT_METRICS_CONFIG = PROJECT_ROOT / "config" / "time_metrics.json"
DEFAULT_CALENDAR_PATH = PROJECT_ROOT / "config" / "work_calendar_cn_2026.json"


@dataclass(frozen=True)
class TimeMetricDefinition:
    code: str
    name: str
    start_field: str
    end_field: str
    unit: str = "minutes"
    enabled: bool = True


def load_metric_definitions(path: Path, metric_code: str | None = None) -> list[TimeMetricDefinition]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_metrics = data.get("metrics")
    if not isinstance(raw_metrics, list):
        raise ValueError(f"Invalid metric config: {path}")

    metrics: list[TimeMetricDefinition] = []
    for item in raw_metrics:
        if not isinstance(item, dict):
            continue
        metric = TimeMetricDefinition(
            code=str(item["code"]),
            name=str(item.get("name") or item["code"]),
            start_field=str(item["start_field"]),
            end_field=str(item["end_field"]),
            unit=str(item.get("unit") or "minutes"),
            enabled=bool(item.get("enabled", True)),
        )
        if not metric.enabled:
            continue
        if metric_code and metric.code != metric_code:
            continue
        metrics.append(metric)
    if metric_code and not metrics:
        raise ValueError(f"Metric not found or disabled: {metric_code}")
    return metrics


def export_month_time_metrics(
    mysql: MySQLConfig,
    year: int,
    month: int,
    output_dir: Path,
    metrics_config_path: Path = DEFAULT_METRICS_CONFIG,
    calendar_path: Path = DEFAULT_CALENDAR_PATH,
    metric_code: str | None = None,
    limit: int | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    month_label = f"{year:04d}-{month:02d}"
    calendar = WorkCalendar.from_json(calendar_path)
    metrics = load_metric_definitions(metrics_config_path, metric_code)
    tickets = _fetch_month_tickets(mysql, month_label, limit)
    field_values = _fetch_metric_field_values(mysql, month_label, [ticket["ticket_id"] for ticket in tickets], metrics)

    rows = [
        _compute_metric_row(ticket, metric, field_values.get(ticket["ticket_id"], {}), calendar)
        for ticket in tickets
        for metric in metrics
    ]
    status_counts = Counter(row["status"] for row in rows)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "month": month_label,
        "ticket_count": len(tickets),
        "metric_count": len(metrics),
        "result_count": len(rows),
        "metrics_config": str(metrics_config_path),
        "calendar": str(calendar_path),
        "work_sessions": [f"{start.isoformat(timespec='minutes')}-{end.isoformat(timespec='minutes')}" for start, end in calendar.work_sessions],
        "summary": {
            "status_counts": dict(sorted(status_counts.items())),
        },
        "metrics": [metric.__dict__ for metric in metrics],
        "rows": rows,
    }
    target = output_path or output_dir / "time_metrics" / f"{month_label}_time_metrics.json"
    write_json(target, report)
    report["output_path"] = str(target)
    return report


def export_ticket_time_metrics(
    mysql: MySQLConfig,
    ticket_id: str,
    output_dir: Path,
    metrics_config_path: Path = DEFAULT_METRICS_CONFIG,
    calendar_path: Path = DEFAULT_CALENDAR_PATH,
    metric_code: str | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    calendar = WorkCalendar.from_json(calendar_path)
    metrics = load_metric_definitions(metrics_config_path, metric_code)
    ticket = _fetch_ticket(mysql, ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket not found in MySQL: {ticket_id}")
    field_values = _fetch_metric_field_values(mysql, ticket["create_month_label"], [ticket["ticket_id"]], metrics)
    rows = [_compute_metric_row(ticket, metric, field_values.get(ticket["ticket_id"], {}), calendar) for metric in metrics]
    status_counts = Counter(row["status"] for row in rows)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ticket_id": ticket_id,
        "ticket": ticket,
        "metric_count": len(metrics),
        "result_count": len(rows),
        "metrics_config": str(metrics_config_path),
        "calendar": str(calendar_path),
        "work_sessions": [f"{start.isoformat(timespec='minutes')}-{end.isoformat(timespec='minutes')}" for start, end in calendar.work_sessions],
        "summary": {
            "status_counts": dict(sorted(status_counts.items())),
        },
        "metrics": [metric.__dict__ for metric in metrics],
        "rows": rows,
    }
    target = output_path or output_dir / "time_metrics" / f"ticket_{ticket_id}_time_metrics.json"
    write_json(target, report)
    report["output_path"] = str(target)
    return report


def _compute_metric_row(
    ticket: dict[str, Any],
    metric: TimeMetricDefinition,
    field_values: dict[str, str | None],
    calendar: WorkCalendar,
) -> dict[str, Any]:
    start_text = _empty_to_none(field_values.get(metric.start_field))
    end_text = _empty_to_none(field_values.get(metric.end_field))
    base = {
        "ticket_id": ticket["ticket_id"],
        "create_dt": ticket.get("create_dt"),
        "create_month_label": ticket.get("create_month_label"),
        "ticket_template_id": ticket.get("ticket_template_id"),
        "subject": ticket.get("subject"),
        "metric_code": metric.code,
        "metric_name": metric.name,
        "start_field": metric.start_field,
        "end_field": metric.end_field,
        "start_time": start_text,
        "end_time": end_text,
        "raw_minutes": None,
        "business_minutes": None,
        "status": "success",
        "error_message": None,
    }
    if not start_text and not end_text:
        return {**base, "status": "missing_both", "error_message": "start and end field values are empty"}
    if not start_text:
        return {**base, "status": "missing_start", "error_message": f"{metric.start_field} is empty"}
    if not end_text:
        return {**base, "status": "missing_end", "error_message": f"{metric.end_field} is empty"}

    start = _parse_datetime(start_text)
    end = _parse_datetime(end_text)
    if start is None:
        return {**base, "status": "invalid_start", "error_message": f"cannot parse {metric.start_field}: {start_text}"}
    if end is None:
        return {**base, "status": "invalid_end", "error_message": f"cannot parse {metric.end_field}: {end_text}"}
    if end < start:
        return {**base, "status": "invalid_time_order", "error_message": "end time is earlier than start time"}

    raw_minutes = int((end - start).total_seconds() // 60)
    business_minutes = business_minutes_between(start, end, calendar)
    return {
        **base,
        "start_time": start.isoformat(sep=" ", timespec="seconds"),
        "end_time": end.isoformat(sep=" ", timespec="seconds"),
        "raw_minutes": raw_minutes,
        "business_minutes": business_minutes,
    }


def _fetch_month_tickets(mysql: MySQLConfig, month_label: str, limit: int | None) -> list[dict[str, Any]]:
    pymysql = _pymysql()
    sql = (
        "SELECT ticket_id, create_dt, create_month_label, ticket_template_id, subject, source_updated_at "
        "FROM ticket_detail_main WHERE create_month_label = %s ORDER BY create_dt, ticket_id"
    )
    params: list[Any] = [month_label]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with pymysql.connect(**_connect_kwargs(mysql)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [_ticket_row(row) for row in cursor.fetchall()]


def _fetch_ticket(mysql: MySQLConfig, ticket_id: str) -> dict[str, Any] | None:
    pymysql = _pymysql()
    with pymysql.connect(**_connect_kwargs(mysql)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ticket_id, create_dt, create_month_label, ticket_template_id, subject, source_updated_at "
                "FROM ticket_detail_main WHERE ticket_id = %s ORDER BY create_dt DESC LIMIT 1",
                (ticket_id,),
            )
            row = cursor.fetchone()
            return _ticket_row(row) if row else None


def _fetch_metric_field_values(
    mysql: MySQLConfig,
    month_label: str,
    ticket_ids: list[int],
    metrics: Iterable[TimeMetricDefinition],
) -> dict[int, dict[str, str | None]]:
    field_keys = sorted({metric.start_field for metric in metrics} | {metric.end_field for metric in metrics})
    if not ticket_ids or not field_keys:
        return {}

    result: dict[int, dict[str, str | None]] = {}
    pymysql = _pymysql()
    with pymysql.connect(**_connect_kwargs(mysql)) as connection:
        with connection.cursor() as cursor:
            for start in range(0, len(ticket_ids), 1000):
                chunk = ticket_ids[start:start + 1000]
                ticket_placeholders = ", ".join(["%s"] * len(chunk))
                field_placeholders = ", ".join(["%s"] * len(field_keys))
                cursor.execute(
                    "SELECT ticket_id, field_key, field_value "
                    "FROM ticket_detail_custom_fields "
                    f"WHERE create_month_label = %s AND ticket_id IN ({ticket_placeholders}) "
                    f"AND field_key IN ({field_placeholders}) "
                    "ORDER BY ticket_id, field_order",
                    [month_label, *chunk, *field_keys],
                )
                for ticket_id, field_key, field_value in cursor.fetchall():
                    values = result.setdefault(int(ticket_id), {})
                    values.setdefault(str(field_key), field_value)
    return result


def _ticket_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "ticket_id": int(row[0]),
        "create_dt": row[1].isoformat(sep=" ", timespec="seconds") if row[1] else None,
        "create_month_label": row[2],
        "ticket_template_id": row[3],
        "subject": row[4],
        "source_updated_at": row[5].isoformat(sep=" ", timespec="seconds") if row[5] else None,
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:len(datetime.now().strftime(fmt))], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _empty_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _connect_kwargs(mysql: MySQLConfig) -> dict[str, Any]:
    return {
        "host": mysql.host,
        "port": mysql.port,
        "user": mysql.user,
        "password": mysql.password,
        "database": mysql.database,
        "charset": "utf8mb4",
    }


def _pymysql() -> Any:
    import pymysql

    return pymysql
