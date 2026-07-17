"""Export readable ERP workbooks directly from database snapshots."""

from __future__ import annotations

from pathlib import Path

from .config import MySQLConfig
from .erp_merge.pipeline import write_document_rows
from .erp_schema import STANDARD_ERP_COLUMN_MAP, standard_headers


def export_erp_snapshot_document(
    config: MySQLConfig, create_date: str, output_file: Path
) -> dict:
    """Stream one ``erp_data`` snapshot into a non-importable document workbook."""
    import pymysql

    columns = [column for _, column in STANDARD_ERP_COLUMN_MAP]
    connection = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.SSCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM erp_data WHERE create_date = %s", (create_date,)
            )
            row_count = int(cursor.fetchone()[0])
            cursor.fetchall()
            if row_count == 0:
                raise ValueError(f"erp_data has no ERP snapshot for create_date={create_date}.")

            cursor.execute(
                f"SELECT {', '.join(columns)} FROM erp_data "
                "WHERE create_date = %s ORDER BY seq_no, id",
                (create_date,),
            )
            write_document_rows(standard_headers(), cursor, output_file)
    finally:
        connection.close()

    return {"create_date": create_date, "rows": row_count, "file": output_file.name}
