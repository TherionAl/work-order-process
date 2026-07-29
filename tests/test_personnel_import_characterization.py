from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from work_order_process import personnel_import
from work_order_process.config import MySQLConfig
from work_order_process.personnel_import import (
    HEADER_COLUMN_MAP,
    PERSONNEL_COLUMNS,
    build_personnel_row,
    import_personnel_xls_to_mysql,
    read_personnel_xls,
    upsert_personnel_rows,
)


class _Sheet:
    def __init__(self, rows: list[list[object]]) -> None:
        self._rows = rows
        self.nrows = len(rows)
        self.ncols = len(rows[0]) if rows else 0

    def cell_value(self, row: int, column: int) -> object:
        return self._rows[row][column]


def _install_workbook(monkeypatch, rows: list[list[object]]) -> None:
    book = SimpleNamespace(sheet_by_index=lambda index: _Sheet(rows))
    monkeypatch.setitem(
        sys.modules,
        "xlrd",
        SimpleNamespace(open_workbook=lambda path: book),
    )


def _mysql() -> MySQLConfig:
    return MySQLConfig("db.local", 3307, "writer", "secret", "work_order")


def test_workbook_normalizes_numeric_employee_numbers_and_ignores_blank_rows(
    monkeypatch, tmp_path: Path
) -> None:
    headers = list(HEADER_COLUMN_MAP)
    _install_workbook(
        monkeypatch,
        [
            headers,
            ["张三", 100012.0, "云南", "客服", "一线组"],
            ["", "", "", "", ""],
            ["李四", " 100013 ", "贵州", "主管", "二线组"],
        ],
    )

    rows = read_personnel_xls(tmp_path / "people.xls")

    assert rows == [
        {
            "employee_no": "100012",
            "person_name": "张三",
            "province": "云南",
            "role_names": "客服",
            "group_name": "一线组",
        },
        {
            "employee_no": "100013",
            "person_name": "李四",
            "province": "贵州",
            "role_names": "主管",
            "group_name": "二线组",
        },
    ]


class _Cursor:
    def __init__(
        self,
        *,
        driver_rowcount: int | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.driver_rowcount = driver_rowcount
        self.failure = failure
        self.sql = ""
        self.values: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def executemany(self, sql: str, values: list[tuple[object, ...]]) -> int:
        self.sql = sql
        self.values = values
        if self.failure:
            raise self.failure
        if self.driver_rowcount is not None:
            return self.driver_rowcount
        return len(values)


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.events: list[str] = []

    def __enter__(self):
        self.events.append("enter")
        return self

    def __exit__(self, *_args) -> None:
        self.events.append("exit")

    def cursor(self) -> _Cursor:
        self.events.append("cursor")
        return self._cursor

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


def _install_database(monkeypatch, connection: _Connection, calls: list[dict]) -> None:
    def connect(**kwargs):
        calls.append(kwargs)
        return connection

    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(connect=connect))


@pytest.mark.parametrize(
    "driver_rowcount",
    [0, 1, 2],
    ids=["unchanged", "inserted", "updated"],
)
def test_unique_personnel_count_ignores_driver_rowcount_and_keeps_latest_row(
    monkeypatch,
    driver_rowcount: int,
) -> None:
    cursor = _Cursor(driver_rowcount=driver_rowcount)
    connection = _Connection(cursor)
    connect_calls: list[dict] = []
    _install_database(monkeypatch, connection, connect_calls)
    rows = [
        build_personnel_row(
            {
                "人员姓名": "旧姓名",
                "工号": 100012.0,
                "所属省份": "云南",
                "角色": "客服",
                "所属组": "一线组",
            }
        ),
        build_personnel_row(
            {
                "人员姓名": "新姓名",
                "工号": "100012",
                "所属省份": "云南",
                "角色": "主管",
                "所属组": "二线组",
            }
        ),
    ]

    affected = upsert_personnel_rows(_mysql(), rows)

    assert affected == 1
    assert "ON DUPLICATE KEY UPDATE" in cursor.sql
    assert cursor.values == [("100012", "新姓名", "云南", "主管", "二线组")]
    assert connection.events == ["enter", "cursor", "commit", "exit"]
    assert connect_calls == [
        {
            "host": "db.local",
            "port": 3307,
            "user": "writer",
            "password": "secret",
            "database": "work_order",
            "charset": "utf8mb4",
            "autocommit": False,
        }
    ]


def test_database_row_failure_rolls_back_and_preserves_error(monkeypatch) -> None:
    cursor = _Cursor(failure=RuntimeError("employee row rejected"))
    connection = _Connection(cursor)
    _install_database(monkeypatch, connection, [])

    with pytest.raises(RuntimeError, match="employee row rejected"):
        upsert_personnel_rows(
            _mysql(),
            [
                {
                    "employee_no": "100012",
                    "person_name": "张三",
                    "province": "云南",
                    "role_names": "客服",
                    "group_name": "一线组",
                }
            ],
        )

    assert connection.events == ["enter", "cursor", "rollback", "exit"]
    assert "commit" not in connection.events


def test_missing_workbook_headers_fail_before_any_database_write(
    monkeypatch, tmp_path: Path
) -> None:
    _install_workbook(monkeypatch, [["人员姓名", "工号"], ["张三", "100012"]])
    monkeypatch.setattr(
        personnel_import,
        "ensure_personnel_schema",
        lambda _: pytest.fail("invalid workbook must not create schema"),
    )
    monkeypatch.setattr(
        personnel_import,
        "upsert_personnel_rows",
        lambda *_: pytest.fail("invalid workbook must not write rows"),
    )

    with pytest.raises(
        ValueError,
        match="Missing personnel headers: 所属省份, 角色, 所属组",
    ):
        import_personnel_xls_to_mysql(_mysql(), tmp_path / "invalid.xls")


def test_import_report_records_source_count_affected_rows_and_call_order(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "people.xls"
    rows = [
        {
            "employee_no": "100012",
            "person_name": "张三",
            "province": "云南",
            "role_names": "客服",
            "group_name": "一线组",
        }
    ]
    calls: list[object] = []
    monkeypatch.setattr(
        personnel_import,
        "read_personnel_xls",
        lambda path: calls.append(("read", path)) or rows,
    )
    monkeypatch.setattr(
        personnel_import,
        "ensure_personnel_schema",
        lambda mysql: calls.append(("schema", mysql)),
    )
    monkeypatch.setattr(
        personnel_import,
        "upsert_personnel_rows",
        lambda mysql, values: calls.append(("upsert", mysql, values)) or 1,
    )

    report = import_personnel_xls_to_mysql(_mysql(), source)

    assert report == {
        "table": "personnel",
        "source_file": str(source),
        "total_count": 1,
        "affected_rows": 1,
    }
    assert calls == [
        ("read", source),
        ("schema", _mysql()),
        ("upsert", _mysql(), rows),
    ]
    assert tuple(PERSONNEL_COLUMNS) == (
        "employee_no",
        "person_name",
        "province",
        "role_names",
        "group_name",
    )
