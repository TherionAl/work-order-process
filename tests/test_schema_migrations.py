from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from work_order_process import cli
from work_order_process.config import MySQLConfig
from work_order_process.schema_migrations import (
    Migration,
    SchemaMigrationError,
    apply_pending_migrations,
    assert_schema_current,
    discover_migrations,
    inspect_schema_status,
    record_satisfied_migrations,
    schema_status,
)
from work_order_process.migrations import (
    v0001_current_schema,
    v0002_erp_allocation_columns,
)


@dataclass
class ScriptedMigrationCursor:
    applied: list[tuple[int, str, str]]

    def execute(self, statement: str, params: object = None) -> None:
        del statement, params

    def fetchall(self) -> list[tuple[int, str, str]]:
        return self.applied


def test_discovered_migrations_are_sorted_and_unique() -> None:
    migrations = discover_migrations()

    assert [migration.version for migration in migrations] == [1, 2]
    assert len({migration.version for migration in migrations}) == 2
    assert all(len(migration.checksum) == 64 for migration in migrations)


def test_applied_checksum_drift_is_rejected() -> None:
    cursor = ScriptedMigrationCursor(
        applied=[(1, "current_schema", "wrong-checksum")]
    )

    with pytest.raises(SchemaMigrationError, match="checksum"):
        inspect_schema_status(cursor, discover_migrations())


class MigrationConnection:
    def __init__(
        self,
        *,
        schema_is_satisfied: bool = False,
        apply_error: Exception | None = None,
    ) -> None:
        self.schema_is_satisfied = schema_is_satisfied
        self.apply_error = apply_error
        self.ddl_statements: list[str] = []
        self.recorded: list[tuple[int, str, str]] = []
        self.commits = 0
        self.rollbacks = 0

    @property
    def recorded_versions(self) -> list[int]:
        return [version for version, _, _ in self.recorded]

    def cursor(self) -> MigrationConnection:
        return self

    def __enter__(self) -> MigrationConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: object = None) -> None:
        if statement.startswith("ALTER TABLE"):
            self.ddl_statements.append(statement)
            self.schema_is_satisfied = True
            if self.apply_error is not None:
                raise self.apply_error
        elif statement.startswith("INSERT INTO schema_version"):
            assert isinstance(params, tuple)
            self.recorded.append(params)

    def fetchall(self) -> list[tuple[int, str, str]]:
        return list(self.recorded)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _migration(version: int) -> Migration:
    def is_satisfied(cursor: MigrationConnection, database: str) -> bool:
        assert database == "work_order_datalake"
        return cursor.schema_is_satisfied

    def apply(cursor: MigrationConnection, database: str) -> None:
        assert database == "work_order_datalake"
        cursor.execute("ALTER TABLE example ADD COLUMN value INT")

    return Migration(
        version=version,
        name=f"migration_{version}",
        checksum=str(version) * 64,
        is_satisfied=is_satisfied,
        apply=apply,
    )


def test_satisfied_existing_schema_is_recorded_without_running_ddl() -> None:
    connection = MigrationConnection(schema_is_satisfied=True)

    status = apply_pending_migrations(
        connection,
        database="work_order_datalake",
        migrations=(_migration(1),),
    )

    assert connection.ddl_statements == []
    assert connection.recorded_versions == [1]
    assert connection.commits == 1
    assert status.is_current


def test_failed_migration_is_not_recorded() -> None:
    connection = MigrationConnection(apply_error=RuntimeError("DDL failed"))

    with pytest.raises(SchemaMigrationError, match="DDL failed"):
        apply_pending_migrations(
            connection,
            database="work_order_datalake",
            migrations=(_migration(1),),
        )

    assert connection.recorded_versions == []
    assert connection.rollbacks == 1


def test_rerun_reconciles_auto_committed_ddl_before_recording_version() -> None:
    connection = MigrationConnection(apply_error=RuntimeError("record unavailable"))
    migration = _migration(1)

    with pytest.raises(SchemaMigrationError, match="auto-commit"):
        apply_pending_migrations(
            connection,
            database="work_order_datalake",
            migrations=(migration,),
        )

    connection.apply_error = None
    status = apply_pending_migrations(
        connection,
        database="work_order_datalake",
        migrations=(migration,),
    )

    assert connection.ddl_statements == [
        "ALTER TABLE example ADD COLUMN value INT"
    ]
    assert connection.recorded_versions == [1]
    assert status.is_current


class InformationSchemaCursor:
    def __init__(
        self,
        *,
        tables: set[str],
        columns: dict[str, set[str]],
    ) -> None:
        self.tables = tables
        self.columns = columns
        self.statements: list[tuple[str, object]] = []
        self.result: list[tuple[object, ...]] = []

    def execute(self, statement: str, params: object = None) -> None:
        self.statements.append((statement, params))
        if "information_schema.tables" in statement.lower():
            self.result = [(table,) for table in sorted(self.tables)]
        elif "information_schema.columns" in statement.lower():
            assert isinstance(params, tuple)
            table = str(params[-1])
            self.result = [
                (column,) for column in sorted(self.columns.get(table, set()))
            ]
        else:
            self.result = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result

    def fetchone(self) -> tuple[object, ...] | None:
        return self.result[0] if self.result else None


def test_current_schema_satisfaction_detects_missing_required_column() -> None:
    tables = {
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
    columns = {
        "ticket_detail_main": {"ticket_category"},
        "customers": {"contact_name", "phone", "email", "row_hash", "sync_batch_id"},
        "contacts": {"fixed_phone", "row_hash", "sync_batch_id"},
    }
    cursor = InformationSchemaCursor(tables=tables, columns=columns)

    assert v0001_current_schema.is_satisfied(cursor, "warehouse")

    columns["contacts"].remove("fixed_phone")
    assert not v0001_current_schema.is_satisfied(cursor, "warehouse")


def test_current_schema_apply_executes_base_table_and_compatibility_ddl() -> None:
    cursor = InformationSchemaCursor(tables=set(), columns={})

    v0001_current_schema.apply(cursor, "warehouse")

    statements = [statement for statement, _ in cursor.statements]
    assert sum("CREATE TABLE IF NOT EXISTS" in statement for statement in statements) == 10
    assert any(
        "ALTER TABLE ticket_detail_main" in statement
        and "ticket_category" in statement
        for statement in statements
    )
    assert any(
        "ALTER TABLE customers" in statement and "`row_hash`" in statement
        for statement in statements
    )


def test_erp_migration_is_satisfied_when_optional_table_is_absent() -> None:
    cursor = InformationSchemaCursor(tables=set(), columns={})

    assert v0002_erp_allocation_columns.is_satisfied(cursor, "warehouse")
    v0002_erp_allocation_columns.apply(cursor, "warehouse")

    assert not [
        statement
        for statement, _ in cursor.statements
        if statement.startswith("ALTER TABLE")
    ]


class StatusCursor:
    def __init__(
        self,
        *,
        version_table_exists: bool,
        applied: list[tuple[int, str, str]] | None = None,
    ) -> None:
        self.version_table_exists = version_table_exists
        self.applied = applied or []
        self.statements: list[str] = []
        self.result: list[tuple[object, ...]] = []

    def __enter__(self) -> StatusCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: object = None) -> None:
        del params
        self.statements.append(statement)
        if "information_schema.tables" in statement:
            self.result = [(1,)] if self.version_table_exists else []
        elif statement.startswith("SELECT version"):
            self.result = list(self.applied)
        else:
            self.result = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result

    def fetchone(self) -> tuple[object, ...] | None:
        return self.result[0] if self.result else None


class StatusConnection:
    def __init__(self, cursor: StatusCursor) -> None:
        self.cursor_instance = cursor

    def __enter__(self) -> StatusConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> StatusCursor:
        return self.cursor_instance


class FakePyMySQL:
    def __init__(self, connection: StatusConnection) -> None:
        self.connection = connection
        self.connect_calls: list[dict[str, object]] = []

    def connect(self, **kwargs: object) -> StatusConnection:
        self.connect_calls.append(kwargs)
        return self.connection


def _config() -> MySQLConfig:
    return MySQLConfig(
        host="db.example",
        port=3306,
        user="user",
        password="secret",
        database="warehouse",
    )


def test_schema_status_without_version_table_is_read_only(monkeypatch) -> None:
    from work_order_process import schema_migrations

    cursor = StatusCursor(version_table_exists=False)
    fake_pymysql = FakePyMySQL(StatusConnection(cursor))
    monkeypatch.setattr(schema_migrations, "pymysql", fake_pymysql)

    status = schema_status(_config())

    assert status.current_version == 0
    assert status.pending_versions == (1, 2)
    assert not any(statement.lstrip().startswith("CREATE") for statement in cursor.statements)


def test_record_satisfied_migrations_does_not_apply_unsatisfied_ddl() -> None:
    connection = MigrationConnection(schema_is_satisfied=False)

    status = record_satisfied_migrations(
        connection,
        database="work_order_datalake",
        migrations=(_migration(1),),
    )

    assert connection.ddl_statements == []
    assert connection.recorded_versions == []
    assert status.pending_versions == (1,)


def test_assert_schema_current_reports_pending_versions(monkeypatch) -> None:
    from work_order_process import schema_migrations

    monkeypatch.setattr(
        schema_migrations,
        "schema_status",
        lambda config: schema_migrations.SchemaStatus(0, 2, (1, 2), ()),
    )

    with pytest.raises(SchemaMigrationError, match=r"pending.*1.*2"):
        assert_schema_current(_config())


def _settings() -> SimpleNamespace:
    return SimpleNamespace(mysql=_config(), dictionary_path="dictionary.pdf")


def _status(*, pending: tuple[int, ...]) -> object:
    from work_order_process.schema_migrations import SchemaStatus

    return SchemaStatus(
        current_version=0 if pending else 2,
        target_version=2,
        pending_versions=pending,
        drifted_versions=(),
    )


def test_schema_status_command_is_read_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_settings", _settings)
    monkeypatch.setattr(cli, "schema_status", lambda config: _status(pending=(2,)))
    monkeypatch.setattr(
        cli.DataDictionary,
        "from_pdf",
        lambda path: pytest.fail("status command must not load the dictionary"),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["work_order_process", "mysql-schema-status"],
    )

    cli.main()

    assert "pending" in capsys.readouterr().out.lower()


def test_mysql_migrate_command_prints_applied_and_remaining(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "load_settings", _settings)
    monkeypatch.setattr(cli, "schema_status", lambda config: _status(pending=(1, 2)))
    monkeypatch.setattr(cli, "migrate_schema", lambda config: _status(pending=()))
    monkeypatch.setattr(
        cli.DataDictionary,
        "from_pdf",
        lambda path: pytest.fail("migration command must not load the dictionary"),
    )
    monkeypatch.setattr("sys.argv", ["work_order_process", "mysql-migrate"])

    cli.main()

    output = capsys.readouterr().out.lower()
    assert "applied" in output
    assert "1" in output and "2" in output
    assert "remaining" in output


def test_database_mutating_command_checks_schema_before_work(monkeypatch) -> None:
    calls: list[MySQLConfig] = []
    monkeypatch.setattr(cli, "load_settings", _settings)
    monkeypatch.setattr(cli, "assert_schema_current", calls.append)
    monkeypatch.setattr(cli, "generate_months_ahead", lambda count: [])
    monkeypatch.setattr(cli, "add_future_partitions", lambda config, months: [])
    monkeypatch.setattr(cli.DataDictionary, "from_pdf", lambda path: object())
    monkeypatch.setattr(
        "sys.argv",
        ["work_order_process", "mysql-add-partitions"],
    )

    cli.main()

    assert calls == [_config()]


def test_mysql_init_records_only_satisfied_baseline(monkeypatch) -> None:
    calls: list[tuple[str, MySQLConfig]] = []
    monkeypatch.setattr(cli, "load_settings", _settings)
    monkeypatch.setattr(
        cli,
        "ensure_mysql_schema",
        lambda config: calls.append(("init", config)),
    )
    monkeypatch.setattr(
        cli,
        "record_satisfied_schema",
        lambda config: (
            calls.append(("record", config))
            or _status(pending=(2,))
        ),
    )
    monkeypatch.setattr(cli, "get_existing_partitions", lambda config: {"pmax"})
    monkeypatch.setattr(
        cli,
        "migrate_schema",
        lambda config: pytest.fail("mysql-init must not apply pending DDL"),
    )
    monkeypatch.setattr(
        cli.DataDictionary,
        "from_pdf",
        lambda path: pytest.fail("mysql-init must not load the dictionary"),
    )
    monkeypatch.setattr("sys.argv", ["work_order_process", "mysql-init"])

    cli.main()

    assert calls == [("init", _config()), ("record", _config())]
