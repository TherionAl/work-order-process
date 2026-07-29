"""Baseline migration for the current work-order schema."""

from __future__ import annotations

from typing import Any

from ..mysql_storage import (
    API_RAW_RECORD_DDL,
    API_SYNC_BATCH_DDL,
    CONTACT_HISTORY_DDL,
    CONTACTS_ALTER_STATEMENTS,
    CONTACTS_DDL,
    CUSTOMER_CONTACT_RELATION_HISTORY_DDL,
    CUSTOMER_HISTORY_DDL,
    CUSTOMERS_ALTER_STATEMENTS,
    CUSTOMERS_DDL,
    SYNC_TASK_LOG_DDL,
    TICKET_DETAIL_CUSTOM_FIELDS_DDL,
    TICKET_DETAIL_MAIN_DDL,
    _add_missing_columns,
    _ensure_ticket_detail_main_columns,
)


VERSION = 1
NAME = "current_schema"

_TABLE_DDLS = (
    TICKET_DETAIL_MAIN_DDL,
    TICKET_DETAIL_CUSTOM_FIELDS_DDL,
    CUSTOMERS_DDL,
    CONTACTS_DDL,
    SYNC_TASK_LOG_DDL,
    CUSTOMER_HISTORY_DDL,
    CONTACT_HISTORY_DDL,
    CUSTOMER_CONTACT_RELATION_HISTORY_DDL,
    API_SYNC_BATCH_DDL,
    API_RAW_RECORD_DDL,
)
_REQUIRED_TABLES = frozenset(
    {
        "ticket_detail_main",
        "ticket_detail_custom_fields",
        "customers",
        "contacts",
        "sync_task_log",
        "customer_history",
        "contact_history",
        "customer_contact_relation_history",
        "api_sync_batch",
        "api_raw_record",
    }
)
_REQUIRED_COLUMNS = {
    "ticket_detail_main": frozenset({"ticket_category"}),
    "customers": frozenset(
        {"contact_name", "phone", "email", "row_hash", "sync_batch_id"}
    ),
    "contacts": frozenset({"fixed_phone", "row_hash", "sync_batch_id"}),
}


def is_satisfied(cursor: Any, database: str) -> bool:
    cursor.execute(
        "SELECT TABLE_NAME FROM information_schema.tables "
        "WHERE table_schema = %s",
        (database,),
    )
    tables = {str(row[0]) for row in cursor.fetchall()}
    if not _REQUIRED_TABLES.issubset(tables):
        return False

    for table, required in _REQUIRED_COLUMNS.items():
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (database, table),
        )
        columns = {str(row[0]) for row in cursor.fetchall()}
        if not required.issubset(columns):
            return False
    return True


def apply(cursor: Any, database: str) -> None:
    for statement in _TABLE_DDLS:
        cursor.execute(statement)
    _ensure_ticket_detail_main_columns(cursor, database)
    _add_missing_columns(
        cursor,
        database,
        "customers",
        CUSTOMERS_ALTER_STATEMENTS,
    )
    _add_missing_columns(
        cursor,
        database,
        "contacts",
        CONTACTS_ALTER_STATEMENTS,
    )
