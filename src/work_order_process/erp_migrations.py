"""Idempotent schema migrations for existing ERP snapshot tables."""

from __future__ import annotations

import pymysql

from .config import MySQLConfig


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


def ensure_erp_allocation_columns(config: MySQLConfig) -> list[str]:
    """Add only missing annual-allocation columns to ``erp_data``."""

    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                (config.database, "erp_data"),
            )
            existing_columns = {row[0] for row in cursor.fetchall()}
            added_columns = []
            for column, definition in ERP_ALLOCATION_COLUMNS.items():
                if column in existing_columns:
                    continue
                cursor.execute(f"ALTER TABLE erp_data ADD COLUMN {column} {definition}")
                added_columns.append(column)
    return added_columns
