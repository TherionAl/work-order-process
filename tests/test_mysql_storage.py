from work_order_process.mysql_storage import (
    API_RAW_RECORD_DDL,
    API_SYNC_BATCH_DDL,
    CONTACT_HISTORY_DDL,
    CONTACTS_ALTER_STATEMENTS,
    CUSTOMER_HISTORY_DDL,
    CUSTOMER_CONTACT_RELATION_HISTORY_DDL,
    CUSTOMERS_ALTER_STATEMENTS,
    CUSTOMER_SERVICE_VIEW_SQL,
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
    assert "CREATE TABLE IF NOT EXISTS customer_contact_relation_history" in CUSTOMER_CONTACT_RELATION_HISTORY_DDL
    assert "CREATE TABLE IF NOT EXISTS api_sync_batch" in API_SYNC_BATCH_DDL
    assert "CREATE TABLE IF NOT EXISTS api_raw_record" in API_RAW_RECORD_DDL
    assert any("ADD COLUMN `row_hash`" in statement for statement in CUSTOMERS_ALTER_STATEMENTS)
    assert any("ADD COLUMN `fixed_phone`" in statement for statement in CONTACTS_ALTER_STATEMENTS)


def test_customer_service_view_uses_ticket_time_and_history_period() -> None:
    assert "CREATE OR REPLACE VIEW v_customer_service_overview" in CUSTOMER_SERVICE_VIEW_SQL
    assert "t.create_dt >= h.effective_from" in CUSTOMER_SERVICE_VIEW_SQL
    assert "h.effective_to IS NULL" in CUSTOMER_SERVICE_VIEW_SQL
