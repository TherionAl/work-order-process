from __future__ import annotations

from typing import Any

import pytest

from work_order_process import mysql_storage, ticket_import
from work_order_process.config import MySQLConfig
from work_order_process.import_failures import FailureCollector


def _config() -> MySQLConfig:
    return MySQLConfig("host", 3306, "user", "password", "database")


class FakeClient:
    def fetch_ticket_fields(self) -> list[dict[str, Any]]:
        return []

    def fetch_company_fields(self) -> list[dict[str, Any]]:
        return []


def test_legacy_month_import_calls_new_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = {"month": "2026-07"}
    monkeypatch.setattr(
        ticket_import,
        "import_month_tickets_to_mysql",
        lambda *args, **kwargs: sentinel,
    )

    assert mysql_storage.import_month_tickets_to_mysql(
        _config(),
        None,
        FakeClient(),
        2026,
        7,
    ) is sentinel


def test_empty_api_month_returns_zero_report_without_schema_or_log_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ticket_import,
        "_fetch_month_ticket_rows",
        lambda *args, **kwargs: ("2026-07", [], "api"),
    )
    monkeypatch.setattr(
        ticket_import,
        "ensure_mysql_schema",
        lambda config: pytest.fail("empty month must not write schema"),
    )
    monkeypatch.setattr(
        ticket_import,
        "write_sync_log",
        lambda *args, **kwargs: pytest.fail("empty month must not write log"),
    )

    report = ticket_import.import_month_tickets_to_mysql(
        _config(), None, FakeClient(), 2026, 7
    )

    assert report == {
        "month": "2026-07",
        "ticket_source": "api",
        "total_in_month": 0,
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "failed_ids": [],
        "failures": [],
        "failures_truncated": False,
    }


def test_current_month_rows_are_skipped_and_logged_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: dict[str, Any] = {}
    rows = [{"ticketId": "1"}, {"ticketId": "2"}, {"ticketId": "3"}]
    monkeypatch.setattr(
        ticket_import,
        "_fetch_month_ticket_rows",
        lambda *args, **kwargs: ("2026-07", rows, "api"),
    )
    monkeypatch.setattr(ticket_import, "ensure_mysql_schema", lambda config: None)
    monkeypatch.setattr(
        ticket_import,
        "_filter_ticket_rows_for_import",
        lambda *args: ([], 3),
    )
    monkeypatch.setattr(
        ticket_import,
        "write_sync_log",
        lambda *args, **kwargs: logged.update(kwargs),
    )

    report = ticket_import.import_month_tickets_to_mysql(
        _config(), None, FakeClient(), 2026, 7
    )

    assert report["skipped"] == 3
    assert report["failed"] == 0
    assert logged["status"] == "success"
    assert logged["skipped_count"] == 3
    assert logged["total_count"] == 3


def test_missing_api_detail_and_database_failure_are_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_failures = FailureCollector()
    api_failures.capture(
        stage="api", exc=RuntimeError("detail missing"), record_id="1"
    )
    database_failures = FailureCollector()
    database_failures.capture(
        stage="database", exc=RuntimeError("row failed"), record_id="2"
    )
    logged: dict[str, Any] = {}
    monkeypatch.setattr(
        ticket_import,
        "_fetch_month_ticket_rows",
        lambda *args, **kwargs: (
            "2026-07", [{"ticketId": "1"}, {"ticketId": "2"}], "api"
        ),
    )
    monkeypatch.setattr(ticket_import, "ensure_mysql_schema", lambda config: None)
    monkeypatch.setattr(
        ticket_import,
        "_filter_ticket_rows_for_import",
        lambda *args: (["1", "2"], 0),
    )
    monkeypatch.setattr(ticket_import, "TicketFieldResolver", lambda *args: object())
    monkeypatch.setattr(ticket_import, "_prefetch_ticket_entities", lambda *args: None)
    monkeypatch.setattr(
        ticket_import,
        "_fetch_batch_details",
        lambda *args, **kwargs: ({"2": ({}, {})}, api_failures),
    )
    monkeypatch.setattr(
        ticket_import,
        "_commit_batch_atomic",
        lambda *args: {
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed_ids": ["2"],
            "failures": database_failures.as_payload()["failures"],
            "failures_truncated": False,
            "custom_rows": 0,
        },
    )
    monkeypatch.setattr(
        ticket_import,
        "write_sync_log",
        lambda *args, **kwargs: logged.update(kwargs),
    )

    report = ticket_import.import_month_tickets_to_mysql(
        _config(), None, FakeClient(), 2026, 7
    )

    assert report["failed_ids"] == ["1", "2"]
    assert [failure["stage"] for failure in report["failures"]] == ["api", "database"]
    assert logged["extra_json"]["failed_ids"] == ["1", "2"]


def test_year_import_returns_twelve_ordered_reports_and_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def import_month(*args: object, **kwargs: object) -> dict[str, Any]:
        month = int(args[4])
        calls.append(month)
        return {
            "month": f"2026-{month:02d}",
            "imported": 1,
            "updated": 2,
            "skipped": 3,
            "failed": 4,
        }

    monkeypatch.setattr(ticket_import, "import_month_tickets_to_mysql", import_month)

    report = ticket_import.import_year_tickets_to_mysql(
        _config(), None, FakeClient(), 2026
    )

    assert calls == list(range(1, 13))
    assert [month["month"] for month in report["months"]] == [
        f"2026-{month:02d}" for month in range(1, 13)
    ]
    assert report == {
        "year": 2026,
        "total_imported": 12,
        "total_updated": 24,
        "total_skipped": 36,
        "total_failed": 48,
        "months": report["months"],
    }
