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
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, sql: str, params: object) -> None:
            executed.append((sql, params))

        def fetchall(self) -> list[dict[str, object]]:
            return [{"id": 2, "status": "success"}]

    class Connection:
        def __enter__(self) -> "Connection":
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
