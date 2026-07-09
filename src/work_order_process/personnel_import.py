"""Import local personnel Excel files into MySQL."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .config import MySQLConfig


HEADER_COLUMN_MAP = {
    "人员姓名": "person_name",
    "工号": "employee_no",
    "所属省份": "province",
    "角色": "role_names",
    "所属组": "group_name",
}

PERSONNEL_COLUMNS = [
    "employee_no",
    "person_name",
    "province",
    "role_names",
    "group_name",
]

PERSONNEL_DDL = """
CREATE TABLE IF NOT EXISTS personnel (
  employee_no VARCHAR(64) NOT NULL COMMENT '工号',
  person_name VARCHAR(255) NULL COMMENT '人员姓名',
  province VARCHAR(100) NULL COMMENT '所属省份',
  role_names TEXT NULL COMMENT '角色',
  group_name VARCHAR(255) NULL COMMENT '所属组',
  last_sync_at TIMESTAMP NULL COMMENT '最近同步时间',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (employee_no),
  KEY idx_person_name (person_name),
  KEY idx_province (province),
  KEY idx_group_name (group_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='人员信息表'
"""


def build_personnel_row(raw_row: dict[str, Any]) -> dict[str, str | None]:
    """Convert one source Excel row to the MySQL personnel shape."""

    row = {
        db_column: _normalize_cell(raw_row.get(header))
        for header, db_column in HEADER_COLUMN_MAP.items()
    }
    if not row["employee_no"]:
        raise ValueError("employee_no is required")
    return row


def read_personnel_xls(path: Path) -> list[dict[str, str | None]]:
    """Read the first sheet of a .xls personnel file."""

    import xlrd

    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    if sheet.nrows == 0:
        return []

    headers = [_normalize_cell(sheet.cell_value(0, col)) or "" for col in range(sheet.ncols)]
    missing_headers = [header for header in HEADER_COLUMN_MAP if header not in headers]
    if missing_headers:
        raise ValueError(f"Missing personnel headers: {', '.join(missing_headers)}")

    rows: list[dict[str, str | None]] = []
    for row_index in range(1, sheet.nrows):
        raw_row = {
            headers[col]: sheet.cell_value(row_index, col)
            for col in range(sheet.ncols)
            if headers[col]
        }
        if not any(_normalize_cell(value) for value in raw_row.values()):
            continue
        rows.append(build_personnel_row(raw_row))
    return rows


def ensure_personnel_schema(config: MySQLConfig) -> None:
    """Create the target database and personnel table when needed."""

    import pymysql

    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        charset="utf8mb4",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{config.database}` "
                "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(f"USE `{config.database}`")
            cursor.execute(PERSONNEL_DDL)


def import_personnel_xls_to_mysql(config: MySQLConfig, path: Path) -> dict[str, Any]:
    """Import a local personnel .xls file into MySQL."""

    rows = read_personnel_xls(path)
    ensure_personnel_schema(config)
    affected = upsert_personnel_rows(config, rows)
    return {
        "table": "personnel",
        "source_file": str(path),
        "total_count": len(rows),
        "affected_rows": affected,
    }


def upsert_personnel_rows(config: MySQLConfig, rows: Iterable[dict[str, Any]]) -> int:
    """Upsert personnel rows by employee_no."""

    import pymysql

    rows = list(rows)
    if not rows:
        return 0

    placeholders = ", ".join(["%s"] * len(PERSONNEL_COLUMNS))
    columns = ", ".join(f"`{column}`" for column in PERSONNEL_COLUMNS)
    updates = ", ".join(
        f"`{column}` = VALUES(`{column}`)"
        for column in PERSONNEL_COLUMNS
        if column != "employee_no"
    )
    sql = (
        f"INSERT INTO personnel ({columns}, last_sync_at) "
        f"VALUES ({placeholders}, CURRENT_TIMESTAMP) "
        f"ON DUPLICATE KEY UPDATE {updates}, last_sync_at = CURRENT_TIMESTAMP"
    )

    values = [
        tuple(row.get(column) for column in PERSONNEL_COLUMNS)
        for row in rows
    ]

    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    ) as connection:
        try:
            with connection.cursor() as cursor:
                affected = cursor.executemany(sql, values)
            connection.commit()
            return int(affected)
        except Exception:
            connection.rollback()
            raise


def _normalize_cell(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None
