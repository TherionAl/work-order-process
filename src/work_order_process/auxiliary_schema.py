"""Ensure the ERP and customer-account tables used by the Excel importers."""

from __future__ import annotations

from pathlib import Path

import pymysql

from .config import MySQLConfig, PROJECT_ROOT
from .erp_migrations import ensure_erp_allocation_columns


def ensure_auxiliary_schema(config: MySQLConfig) -> None:
    """Create the two snapshot tables when they are missing.

    Existing tables are intentionally left unchanged. This keeps the helper
    idempotent and avoids altering a populated production table implicitly.
    """

    sql_dir = PROJECT_ROOT / "sql"
    statements = [
        (sql_dir / "erp_data.sql").read_text(encoding="utf-8"),
        (sql_dir / "customer_account.sql").read_text(encoding="utf-8"),
    ]
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
            for statement in statements:
                cursor.execute(statement)
    ensure_erp_allocation_columns(config)
