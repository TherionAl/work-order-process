"""Idempotent schema migrations for existing ERP snapshot tables."""

from __future__ import annotations

import pymysql

from .config import MySQLConfig
from .migrations.v0002_erp_allocation_columns import (
    apply,
    is_satisfied,
    missing_columns,
)


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
            if is_satisfied(cursor, config.database):
                return []
            added_columns = missing_columns(cursor, config.database)
            apply(cursor, config.database)
            return added_columns
