"""Add the annual-allocation columns to an existing ERP snapshot table."""

from __future__ import annotations

from typing import Any


VERSION = 2
NAME = "erp_allocation_columns"

ERP_ALLOCATION_COLUMNS = {
    "contract_days": "INT NULL COMMENT '合同天数'",
    "prev_year_period_start": "DATE NULL COMMENT '去年统计起始日期'",
    "prev_year_period_end": "DATE NULL COMMENT '去年统计截止日期'",
    "prev_year_calc_amort": "DECIMAL(18,2) NULL COMMENT '去年按期分摊服务费'",
    "prev_year_adjusted_amort": "DECIMAL(18,2) NULL COMMENT '去年倒签调整后分摊服务费'",
    "cur_year_period_start": "DATE NULL COMMENT '今年统计起始日期'",
    "cur_year_period_end": "DATE NULL COMMENT '今年统计截止日期'",
    "cur_year_calc_amort": "DECIMAL(18,2) NULL COMMENT '今年按期分摊服务费'",
    "cur_year_adjusted_amort": "DECIMAL(18,2) NULL COMMENT '今年倒签调整后分摊服务费'",
}


def _table_exists(cursor: Any, database: str) -> bool:
    cursor.execute(
        "SELECT TABLE_NAME FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s",
        (database, "erp_data"),
    )
    return cursor.fetchone() is not None


def missing_columns(cursor: Any, database: str) -> list[str]:
    if not _table_exists(cursor, database):
        return []
    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (database, "erp_data"),
    )
    existing = {str(row[0]) for row in cursor.fetchall()}
    return [
        column
        for column in ERP_ALLOCATION_COLUMNS
        if column not in existing
    ]


def is_satisfied(cursor: Any, database: str) -> bool:
    return not missing_columns(cursor, database)


def apply(cursor: Any, database: str) -> None:
    for column in missing_columns(cursor, database):
        cursor.execute(
            f"ALTER TABLE erp_data ADD COLUMN {column} "
            f"{ERP_ALLOCATION_COLUMNS[column]}"
        )
