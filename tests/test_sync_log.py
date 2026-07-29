from __future__ import annotations

import pytest

from work_order_process import mysql_storage, sync_log
from work_order_process.config import MySQLConfig


def _config() -> MySQLConfig:
    return MySQLConfig("host", 3306, "user", "password", "database")


def test_mysql_storage_reexports_sync_log_ddl() -> None:
    assert mysql_storage.SYNC_TASK_LOG_DDL == sync_log.SYNC_TASK_LOG_DDL


def test_read_sync_logs_rejects_nonpositive_and_unbounded_limits() -> None:
    for limit in (0, -1, sync_log.MAX_SYNC_LOG_READ_LIMIT + 1):
        with pytest.raises(ValueError, match="limit"):
            sync_log.read_sync_logs(_config(), limit)


def test_read_sync_logs_returns_latest_rows_from_fake_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[tuple[str, object]] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: object) -> None:
            executed.append((sql, params))

        def fetchall(self) -> list[dict[str, object]]:
            return [{"id": 2, "status": "success"}]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

    class PyMySQL:
        class cursors:
            DictCursor = object

        @staticmethod
        def connect(**kwargs: object) -> Connection:
            assert kwargs["database"] == "database"
            return Connection()

    monkeypatch.setattr(sync_log, "pymysql", PyMySQL)

    assert sync_log.read_sync_logs(_config(), 20) == [{"id": 2, "status": "success"}]
    assert executed[0][1] == (20,)
    assert "ORDER BY id DESC" in executed[0][0]


def test_write_sync_log_uses_autocommit_parameterized_insert_and_utf8_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_calls: list[dict[str, object]] = []
    executions: list[tuple[str, tuple[object, ...]]] = []
    events: list[str] = []

    class Cursor:
        def __enter__(self):
            events.append("cursor-enter")
            return self

        def __exit__(self, *_args) -> None:
            events.append("cursor-exit")

        def execute(self, sql: str, params: tuple[object, ...]) -> None:
            executions.append((sql, params))

    class Connection:
        def __enter__(self):
            events.append("connection-enter")
            return self

        def __exit__(self, *_args) -> None:
            events.append("connection-exit")

        def cursor(self) -> Cursor:
            return Cursor()

    class PyMySQL:
        @staticmethod
        def connect(**kwargs: object) -> Connection:
            connect_calls.append(kwargs)
            return Connection()

    monkeypatch.setattr(sync_log, "pymysql", PyMySQL)

    sync_log.write_sync_log(
        _config(),
        task_type="ticket_detail",
        target_year=2026,
        target_month=7,
        month_label="2026-07",
        status="partial",
        total_count=5,
        success_count=3,
        failed_count=1,
        skipped_count=1,
        duration_seconds=9,
        error_message="一条失败",
        extra_json={"说明": "客户同步完成"},
    )
    sync_log.write_sync_log(
        _config(),
        task_type="customer",
        month_label="2026-07",
        status="success",
        extra_json=None,
    )

    expected_connect = {
        "host": "host",
        "port": 3306,
        "user": "user",
        "password": "password",
        "database": "database",
        "charset": "utf8mb4",
        "autocommit": True,
    }
    assert connect_calls == [expected_connect, expected_connect]
    assert events == [
        "connection-enter",
        "cursor-enter",
        "cursor-exit",
        "connection-exit",
        "connection-enter",
        "cursor-enter",
        "cursor-exit",
        "connection-exit",
    ]
    sql, params = executions[0]
    assert "INSERT INTO sync_task_log" in sql
    assert "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)" in sql
    assert "客户同步完成" not in sql
    assert params == (
        "ticket_detail",
        2026,
        7,
        "2026-07",
        "partial",
        5,
        3,
        1,
        1,
        9,
        "一条失败",
        '{"说明": "客户同步完成"}',
    )
    assert executions[1][1] == (
        "customer",
        None,
        None,
        "2026-07",
        "success",
        0,
        0,
        0,
        0,
        None,
        None,
        None,
    )
