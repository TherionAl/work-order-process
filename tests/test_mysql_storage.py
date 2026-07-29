import threading
from typing import Any

from work_order_process.mysql_storage import (
    API_RAW_RECORD_DDL,
    API_SYNC_BATCH_DDL,
    CONTACT_HISTORY_DDL,
    CONTACTS_ALTER_STATEMENTS,
    CUSTOMER_CONTACT_RELATION_HISTORY_DDL,
    CUSTOMER_HISTORY_DDL,
    CUSTOMER_SERVICE_VIEW_SQL,
    CUSTOMERS_ALTER_STATEMENTS,
    _commit_batch,
    _fetch_batch_details,
    build_ticket_detail_main_row,
)


def test_build_ticket_detail_main_row_defaults_ticket_category() -> None:
    row = build_ticket_detail_main_row(
        {
            "ticketId": "1",
            "createDT": "2026-01-01 00:00:00",
        }
    )

    assert row["ticket_category"] == "\u539f\u5355"


def test_build_ticket_detail_main_row_uses_resolved_ticket_category() -> None:
    row = build_ticket_detail_main_row(
        {
            "ticketId": "1",
            "createDT": "2026-01-01 00:00:00",
            "ticket_category": "\u5b50\u5355",
        }
    )

    assert row["ticket_category"] == "\u5b50\u5355"


def test_customer_contact_analytics_schema_defines_history_batch_and_columns() -> None:
    assert "CREATE TABLE IF NOT EXISTS customer_history" in CUSTOMER_HISTORY_DDL
    assert "CREATE TABLE IF NOT EXISTS contact_history" in CONTACT_HISTORY_DDL
    assert (
        "CREATE TABLE IF NOT EXISTS customer_contact_relation_history"
        in CUSTOMER_CONTACT_RELATION_HISTORY_DDL
    )
    assert "CREATE TABLE IF NOT EXISTS api_sync_batch" in API_SYNC_BATCH_DDL
    assert "CREATE TABLE IF NOT EXISTS api_raw_record" in API_RAW_RECORD_DDL
    assert any("ADD COLUMN `row_hash`" in statement for statement in CUSTOMERS_ALTER_STATEMENTS)
    assert any("ADD COLUMN `fixed_phone`" in statement for statement in CONTACTS_ALTER_STATEMENTS)


def test_customer_service_view_uses_ticket_time_and_history_period() -> None:
    assert "CREATE OR REPLACE VIEW v_customer_service_overview" in CUSTOMER_SERVICE_VIEW_SQL
    assert "t.create_dt >= h.effective_from" in CUSTOMER_SERVICE_VIEW_SQL
    assert "h.effective_to IS NULL" in CUSTOMER_SERVICE_VIEW_SQL


class FailingTicketClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def fetch_ticket_detail(self, _: str) -> dict[str, Any]:
        raise self.error


class FakeResolver:
    pass


def test_fetch_batch_details_returns_api_failure_reason() -> None:
    client = FailingTicketClient(RuntimeError("temporary API failure"))

    details, failures = _fetch_batch_details(
        client,
        ["T1"],
        FakeResolver(),
        threading.Semaphore(1),
        max_workers=1,
    )

    assert details == {}
    assert failures.as_payload()["failures"][0]["record_id"] == "T1"
    assert failures.as_payload()["failures"][0]["stage"] == "api"


class RowFailingCursor:
    def __init__(self, ticket_id: str) -> None:
        self.ticket_id = ticket_id

    def __enter__(self) -> RowFailingCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: object | None = None) -> None:
        if (
            sql.startswith("INSERT INTO ticket_detail_main")
            and isinstance(params, list)
            and str(params[0]) == self.ticket_id
        ):
            raise RuntimeError("row write failed")

    def fetchone(self) -> None:
        return None


class RowFailingConnection:
    def __init__(self, ticket_id: str) -> None:
        self.cursor_instance = RowFailingCursor(ticket_id)

    def cursor(self) -> RowFailingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _detail_map(*ticket_ids: str) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {
        ticket_id: (
            {"ticketId": ticket_id, "createDT": "2026-07-29 00:00:00"},
            {"ticketId": ticket_id, "createDT": "2026-07-29 00:00:00"},
        )
        for ticket_id in ticket_ids
    }


def test_commit_batch_returns_database_failure_reason() -> None:
    connection = RowFailingConnection(ticket_id="2")

    report = _commit_batch(connection, _detail_map("1", "2"))

    assert report["failed_ids"] == ["2"]
    assert report["failures"][0]["record_id"] == "2"
    assert report["failures"][0]["stage"] == "database"
