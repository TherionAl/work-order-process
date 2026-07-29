from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
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

    assert (
        mysql_storage.import_month_tickets_to_mysql(
            _config(),
            None,
            FakeClient(),
            2026,
            7,
        )
        is sentinel
    )


def test_legacy_year_import_delegates_all_scope_and_tuning_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = {"year": 2026}
    calls: list[tuple[object, ...]] = []

    def implementation(*args: object, **kwargs: object) -> dict[str, int]:
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(ticket_import, "import_year_tickets_to_mysql", implementation)
    client = FakeClient()

    result = mysql_storage.import_year_tickets_to_mysql(
        _config(),
        None,
        client,
        2026,
        months=[2, 4],
        per_page=50,
        limit_per_month=7,
        max_workers=3,
        batch_size=20,
        api_rate_limit=4,
        output_dir=None,
    )

    assert result is sentinel
    assert calls == [
        (
            (_config(), None, client, 2026),
            {
                "months": [2, 4],
                "per_page": 50,
                "limit_per_month": 7,
                "max_workers": 3,
                "batch_size": 20,
                "api_rate_limit": 4,
                "output_dir": None,
            },
        )
    ]


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

    report = ticket_import.import_month_tickets_to_mysql(_config(), None, FakeClient(), 2026, 7)

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

    report = ticket_import.import_month_tickets_to_mysql(_config(), None, FakeClient(), 2026, 7)

    assert report["skipped"] == 3
    assert report["failed"] == 0
    assert logged["status"] == "success"
    assert logged["skipped_count"] == 3
    assert logged["total_count"] == 3


def test_missing_api_detail_and_database_failure_are_structured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_failures = FailureCollector()
    api_failures.capture(stage="api", exc=RuntimeError("detail missing"), record_id="1")
    database_failures = FailureCollector()
    database_failures.capture(stage="database", exc=RuntimeError("row failed"), record_id="2")
    logged: dict[str, Any] = {}
    monkeypatch.setattr(
        ticket_import,
        "_fetch_month_ticket_rows",
        lambda *args, **kwargs: ("2026-07", [{"ticketId": "1"}, {"ticketId": "2"}], "api"),
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

    report = ticket_import.import_month_tickets_to_mysql(_config(), None, FakeClient(), 2026, 7)

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

    report = ticket_import.import_year_tickets_to_mysql(_config(), None, FakeClient(), 2026)

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


class MonthlySearchClient(FakeClient):
    def __init__(self) -> None:
        self.searches: list[tuple[str, int, int]] = []

    def search_tickets_by_create_month(
        self, month_label: str, page: int, per_page: int
    ) -> dict[str, Any]:
        self.searches.append((month_label, page, per_page))
        return {
            "count": 2,
            "results": [{"ticketId": "1"}, {"ticketId": "2"}] if page == 1 else [],
        }


def test_month_ticket_row_adapter_preserves_api_rows_and_scope() -> None:
    client = MonthlySearchClient()

    month, rows, source = ticket_import._fetch_month_ticket_rows(
        client,
        2026,
        7,
        per_page=10,
        limit_per_month=2,
    )

    assert (month, rows, source) == (
        "2026-07",
        [{"ticketId": "1"}, {"ticketId": "2"}],
        "api",
    )
    assert client.searches == [("2026-07", 1, 10)]


class SerialDetailClient(FakeClient):
    def __init__(self) -> None:
        self.detail_calls: list[str] = []

    def fetch_ticket_detail(self, ticket_id: str) -> dict[str, Any] | None:
        self.detail_calls.append(ticket_id)
        if ticket_id == "missing":
            return None
        if ticket_id == "api-error":
            raise RuntimeError("detail endpoint failed")
        return {"ticketId": ticket_id, "custom_fields": []}


def test_serial_import_preserves_successes_and_structures_api_and_database_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket_ids = ["insert", "update", "missing", "api-error", "db-error"]
    rows = [{"ticketId": ticket_id} for ticket_id in ticket_ids]
    calls: list[object] = []
    client = SerialDetailClient()
    monkeypatch.setattr(
        ticket_import,
        "_fetch_month_ticket_rows",
        lambda *args, **kwargs: ("2026-07", rows, "api"),
    )
    monkeypatch.setattr(
        ticket_import,
        "ensure_mysql_schema",
        lambda config: calls.append(("schema", config)),
    )
    monkeypatch.setattr(
        ticket_import,
        "_filter_ticket_rows_for_import",
        lambda config, values, month: (ticket_ids, 0),
    )
    monkeypatch.setattr(ticket_import, "TicketFieldResolver", lambda *args: "resolver")
    monkeypatch.setattr(
        ticket_import,
        "resolve_ticket_detail_values",
        lambda raw, api_client, resolver: {**raw, "resolved": True},
    )

    def commit(config, details):
        ticket_id = next(iter(details))
        calls.append(("commit", ticket_id, details[ticket_id][1]["resolved"]))
        if ticket_id == "db-error":
            raise RuntimeError("database rejected row")
        return {
            "imported": int(ticket_id == "insert"),
            "updated": int(ticket_id == "update"),
            "skipped": 0,
            "failed_ids": [],
            "failures": [],
            "failures_truncated": False,
            "custom_rows": 2,
        }

    monkeypatch.setattr(ticket_import, "_commit_batch_atomic", commit)
    logged: dict[str, Any] = {}
    monkeypatch.setattr(
        ticket_import,
        "write_sync_log",
        lambda *args, **kwargs: logged.update(kwargs),
    )

    report = ticket_import.import_month_tickets_serial(
        _config(),
        None,
        client,
        2026,
        7,
    )

    assert report["imported"] == 1
    assert report["updated"] == 1
    assert report["failed"] == 3
    assert report["failed_ids"] == ["missing", "api-error", "db-error"]
    assert [failure["stage"] for failure in report["failures"]] == [
        "api",
        "api",
        "database",
    ]
    assert report["custom_field_rows"] == 4
    assert client.detail_calls == ticket_ids
    assert calls == [
        ("schema", _config()),
        ("commit", "insert", True),
        ("commit", "update", True),
        ("commit", "db-error", True),
    ]
    assert logged["status"] == "partial"
    assert logged["success_count"] == 2
    assert logged["failed_count"] == 3
    assert logged["extra_json"]["failed_ids"] == ["missing", "api-error", "db-error"]


def test_serial_import_all_current_logs_prefiltered_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"ticketId": "1"}, {"ticketId": "2"}]
    client = FakeClient()
    logged: dict[str, Any] = {}
    monkeypatch.setattr(
        ticket_import,
        "_fetch_month_ticket_rows",
        lambda *args, **kwargs: ("2026-07", rows, "api"),
    )
    monkeypatch.setattr(ticket_import, "ensure_mysql_schema", lambda config: None)
    monkeypatch.setattr(
        ticket_import,
        "_filter_ticket_rows_for_import",
        lambda *args: ([], 2),
    )
    monkeypatch.setattr(
        ticket_import,
        "write_sync_log",
        lambda *args, **kwargs: logged.update(kwargs),
    )

    report = ticket_import.import_month_tickets_serial(_config(), None, client, 2026, 7)

    assert report["skipped"] == 2
    assert report["total_in_month"] == 2
    assert report["custom_field_rows"] == 0
    assert logged["status"] == "success"
    assert logged["extra_json"] == {"ticket_source": "api", "prefiltered": True}


def test_prefetch_collects_unique_referenced_entities_and_rate_limiter() -> None:
    captured: dict[str, Any] = {}

    class PrefetchClient:
        def prefetch_entities(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    ticket_import._prefetch_ticket_entities(
        PrefetchClient(),
        [
            {
                "custUserId": "C1",
                "servicerUserId": "S1",
                "createrId": "S2",
                "deleterId": "0",
                "servicerGroupId": "G1",
                "ticketTemplateId": "TP1",
                "ccUserIdList": "S2,S3",
                "ccGroupIdList": "G1,G2",
            },
            {"custUserId": "C1", "servicerUserId": "S1"},
        ],
        object(),
        max_workers=3,
        api_rate_limit=2,
    )

    assert captured["contacts"] == {"C1"}
    assert captured["companies"] == set()
    assert captured["supports"] == {"S1", "S2", "S3"}
    assert captured["groups"] == {"G1", "G2"}
    assert captured["templates"] == {"TP1"}
    assert captured["max_workers"] == 3
    assert captured["semaphore"].acquire(blocking=False) is True
    assert captured["semaphore"].acquire(blocking=False) is True
    assert captured["semaphore"].acquire(blocking=False) is False


class FilterCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, list[object]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params: list[object]) -> None:
        self.executions.append((sql, params))

    def fetchall(self):
        return [
            ("same", datetime(2026, 7, 1, 12, 0)),
            ("changed", datetime(2026, 7, 1, 11, 0)),
        ]


class FilterConnection:
    def __init__(self, cursor: FilterCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> FilterCursor:
        return self._cursor


def test_ticket_prefilter_skips_same_timestamp_and_keeps_missing_or_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FilterCursor()
    connection = FilterConnection(cursor)
    connect_calls: list[dict[str, Any]] = []

    def connect(**kwargs: Any) -> FilterConnection:
        connect_calls.append(kwargs)
        return connection

    monkeypatch.setattr(
        ticket_import,
        "_pymysql",
        lambda: SimpleNamespace(connect=connect),
    )

    pending, skipped = ticket_import._filter_ticket_rows_for_import(
        _config(),
        [
            {"ticketId": "", "updateDT": "2026-07-01 12:00:00"},
            {"ticketId": "unknown", "updateDT": None},
            {"ticketId": "same", "updateDT": "2026-07-01 12:00:00"},
            {"ticketId": "changed", "updateDT": "2026-07-01 12:00:00"},
        ],
        "2026-07",
    )

    assert pending == ["unknown", "changed"]
    assert skipped == 1
    assert cursor.executions[0][1] == [
        "2026-07",
        "unknown",
        "same",
        "changed",
    ]
    assert connect_calls[0]["autocommit"] is True


def test_atomic_connection_failure_reports_each_ticket_without_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ticket_import,
        "_pymysql",
        lambda: SimpleNamespace(
            connect=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable"))
        ),
    )
    details = {
        "T1": ({"ticketId": "T1"}, {"ticketId": "T1"}),
        "T2": ({"ticketId": "T2"}, {"ticketId": "T2"}),
    }

    report = ticket_import._commit_batch_atomic(_config(), details)

    assert report["imported"] == 0
    assert report["failed_ids"] == ["T1", "T2"]
    assert [failure["record_id"] for failure in report["failures"]] == ["T1", "T2"]
    assert all(failure["stage"] == "database" for failure in report["failures"])


class CommitCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class CommitConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.cursor_instance = CommitCursor()

    def cursor(self) -> CommitCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_batch_commit_counts_insert_update_skip_and_custom_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = CommitConnection()
    details = {
        action: (
            {"ticketId": action, "custom_fields": []},
            {"ticketId": action, "action": action},
        )
        for action in ("inserted", "updated", "skipped")
    }
    monkeypatch.setattr(
        ticket_import,
        "build_ticket_detail_main_row",
        lambda value: {"ticket_id": value["ticketId"], "action": value["action"]},
    )
    monkeypatch.setattr(
        ticket_import,
        "build_ticket_detail_custom_field_rows",
        lambda raw, value: [{"field_key": "x"}],
    )
    monkeypatch.setattr(
        ticket_import,
        "_upsert_ticket_detail",
        lambda cursor, main, custom: main["action"],
    )

    report = ticket_import._commit_batch(connection, details)

    assert report == {
        "imported": 1,
        "updated": 1,
        "skipped": 1,
        "failed_ids": [],
        "failures": [],
        "failures_truncated": False,
        "custom_rows": 2,
    }
    assert connection.commits == 1
    assert connection.rollbacks == 0
