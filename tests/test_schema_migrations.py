from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from work_order_process import cli
from work_order_process.config import MySQLConfig
from work_order_process.migrations import (
    v0001_current_schema,
    v0002_erp_allocation_columns,
)
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


@dataclass
class ScriptedMigrationCursor:
    applied: list[tuple[int, str, str]]

    def execute(self, statement: str, params: object = None) -> None:
        del statement, params

    def fetchall(self) -> list[tuple[int, str, str]]:
        return self.applied


def test_discovered_migrations_are_sorted_and_unique() -> None:
    migrations = discover_migrations()

    assert [migration.version for migration in migrations] == [1, 2, 3, 4, 5]
    assert len({migration.version for migration in migrations}) == 5
    assert all(len(migration.checksum) == 64 for migration in migrations)


def _discovered_migration(version: int) -> Migration:
    migration = next(
        (migration for migration in discover_migrations() if migration.version == version),
        None,
    )
    if migration is None:
        pytest.fail(f"migration {version} is not discoverable")
    return migration


class ManagedObjectCursor:
    """Small information-schema fake that applies frozen migration DDL."""

    def __init__(self) -> None:
        self.columns: dict[str, list[str]] = {}
        self.scales: dict[tuple[str, str], int | None] = {}
        self.indexes: dict[str, dict[str, tuple[int, tuple[str, ...]]]] = {}
        self.partitions: dict[str, dict[str, str]] = {}
        self.views: set[str] = set()
        self.view_definitions: dict[str, str] = {}
        self.statements: list[str] = []
        self.result: list[tuple[object, ...]] = []

    def execute(self, statement: str, params: object = None) -> None:
        self.statements.append(statement)
        lowered = statement.lower()
        if "information_schema.columns" in lowered:
            assert isinstance(params, tuple)
            table = str(params[-1])
            names = self.columns.get(table, [])
            if "numeric_scale" in lowered:
                self.result = [
                    (name, self.scales.get((table, name)), position)
                    for position, name in enumerate(names, start=1)
                ]
            else:
                self.result = [(name,) for name in names]
            return
        if "information_schema.statistics" in lowered:
            assert isinstance(params, tuple)
            table = str(params[-1])
            self.result = [
                (index_name, non_unique, position, column)
                for index_name, (non_unique, columns) in sorted(self.indexes.get(table, {}).items())
                for position, column in enumerate(columns, start=1)
            ]
            return
        if "information_schema.partitions" in lowered:
            assert isinstance(params, tuple)
            table = str(params[-1])
            self.result = list(self.partitions.get(table, {}).items())
            return
        if "information_schema.views" in lowered:
            assert isinstance(params, tuple)
            view = str(params[-1])
            self.result = (
                [(self.view_definitions.get(view, "stale view definition"),)]
                if view in self.views
                else []
            )
            return
        create_table = re.search(
            r"CREATE TABLE IF NOT EXISTS\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
            statement,
            re.IGNORECASE,
        )
        if create_table:
            table = create_table.group(1)
            if table not in self.columns:
                ignored = {"PRIMARY", "UNIQUE", "KEY", "INDEX", "PARTITION"}
                names: list[str] = []
                for line in statement.splitlines():
                    match = re.match(r"\s{2}`?([A-Za-z_][A-Za-z0-9_]*)`?\s+", line)
                    if match and match.group(1).upper() not in ignored:
                        name = match.group(1)
                        names.append(name)
                        scale = re.search(r"DECIMAL\(\d+,(\d+)\)", line, re.IGNORECASE)
                        self.scales[(table, name)] = int(scale.group(1)) if scale else None
                self.columns[table] = names
                indexes: dict[str, tuple[int, tuple[str, ...]]] = {}
                for line in statement.splitlines():
                    index_match = re.match(
                        r"\s{2}(PRIMARY KEY|UNIQUE KEY|KEY|INDEX)(?:\s+`?([A-Za-z_][A-Za-z0-9_]*)`?)?\s*\(([^)]+)\)",
                        line,
                        re.IGNORECASE,
                    )
                    if index_match is None:
                        continue
                    kind, name, raw_columns = index_match.groups()
                    index_name = "PRIMARY" if kind.upper() == "PRIMARY KEY" else str(name)
                    non_unique = 0 if kind.upper() in {"PRIMARY KEY", "UNIQUE KEY"} else 1
                    index_columns = tuple(
                        value.strip().strip("`").split("(", maxsplit=1)[0]
                        for value in raw_columns.split(",")
                    )
                    indexes[index_name] = (non_unique, index_columns)
                self.indexes[table] = indexes
                self.partitions[table] = {
                    match.group(1): match.group(2).upper()
                    for line in statement.splitlines()
                    if (
                        match := re.match(
                            r"\s{2}PARTITION\s+([A-Za-z_][A-Za-z0-9_]*)\s+"
                            r"VALUES LESS THAN\s*\(([^)]+)\)",
                            line,
                            re.IGNORECASE,
                        )
                    )
                }
            self.result = []
            return
        create_view = re.search(
            r"CREATE OR REPLACE VIEW\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
            statement,
            re.IGNORECASE,
        )
        if create_view:
            view = create_view.group(1)
            self.views.add(view)
            self.view_definitions[view] = statement
            self.result = []
            return
        if lowered.startswith("alter table ops_service_revenue_monthly"):
            for column in (
                "revenue_target",
                "recognized_revenue",
                "contracts_on_hand_amount",
                "prior_year_contracts_on_hand_amount",
                "contracts_on_hand_yoy_amount",
                "recognized_revenue_excluding_estimate",
                "prior_year_recognized_revenue",
                "recognized_revenue_yoy_amount",
                "signing_completed_amount",
                "prior_year_signing_amount",
                "signing_yoy_amount",
            ):
                if f"MODIFY COLUMN {column}" in statement:
                    self.scales[("ops_service_revenue_monthly", column)] = 0
            if "MODIFY COLUMN erp_create_date" in statement:
                names = self.columns["ops_service_revenue_monthly"]
                names.remove("erp_create_date")
                names.insert(names.index("created_at"), "erp_create_date")
            self.result = []
            return
        self.result = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result

    def fetchone(self) -> tuple[object, ...] | None:
        return self.result[0] if self.result else None


@pytest.mark.parametrize(
    ("version", "tables", "required_columns"),
    [
        (3, ("erp_data", "customer_account"), ("contract_id", "annual_ops_fee")),
        (4, ("personnel",), ("employee_no", "last_sync_at")),
    ],
)
def test_object_migration_creates_missing_tables_and_then_is_satisfied(
    version: int,
    tables: tuple[str, ...],
    required_columns: tuple[str, ...],
) -> None:
    migration = _discovered_migration(version)
    cursor = ManagedObjectCursor()

    assert not migration.is_satisfied(cursor, "warehouse")
    migration.apply(cursor, "warehouse")

    assert migration.is_satisfied(cursor, "warehouse")
    assert set(tables).issubset(cursor.columns)
    assert all(
        any(column in columns for columns in cursor.columns.values()) for column in required_columns
    )


@pytest.mark.parametrize(
    ("version", "table"),
    [(3, "erp_data"), (4, "personnel"), (5, "ops_service_revenue_monthly")],
)
def test_object_migration_rejects_partial_existing_table(version: int, table: str) -> None:
    migration = _discovered_migration(version)
    cursor = ManagedObjectCursor()
    cursor.columns[table] = ["id"]

    assert not migration.is_satisfied(cursor, "warehouse")
    with pytest.raises(RuntimeError, match=r"missing required columns.*manual repair"):
        migration.apply(cursor, "warehouse")


def test_revenue_migration_creates_table_and_total_view() -> None:
    migration = _discovered_migration(5)
    cursor = ManagedObjectCursor()

    migration.apply(cursor, "warehouse")

    assert migration.is_satisfied(cursor, "warehouse")
    assert "ops_service_revenue_monthly" in cursor.columns
    assert "v_ops_service_revenue_monthly_with_total" in cursor.views


def test_revenue_migration_safely_corrects_legacy_scale_order_and_view() -> None:
    migration = _discovered_migration(5)
    cursor = ManagedObjectCursor()
    migration.apply(cursor, "warehouse")
    cursor.views.clear()
    cursor.view_definitions.clear()
    money_column = "recognized_revenue"
    cursor.scales[("ops_service_revenue_monthly", money_column)] = 2
    names = cursor.columns["ops_service_revenue_monthly"]
    names.remove("erp_create_date")
    names.insert(0, "erp_create_date")
    cursor.statements.clear()

    assert not migration.is_satisfied(cursor, "warehouse")
    migration.apply(cursor, "warehouse")

    assert migration.is_satisfied(cursor, "warehouse")
    assert any(
        statement.startswith("UPDATE ops_service_revenue_monthly")
        for statement in cursor.statements
    )
    assert any(
        "MODIFY COLUMN recognized_revenue DECIMAL(18,0)" in statement
        for statement in cursor.statements
    )
    assert any("MODIFY COLUMN erp_create_date" in statement for statement in cursor.statements)
    assert any("CREATE OR REPLACE VIEW" in statement for statement in cursor.statements)


@pytest.mark.parametrize(
    ("version", "table", "missing_index"),
    (
        (3, "erp_data", "uk_snapshot_line"),
        (3, "erp_data", "PRIMARY"),
        (3, "customer_account", "PRIMARY"),
        (4, "personnel", "PRIMARY"),
        (5, "ops_service_revenue_monthly", "PRIMARY"),
        (5, "ops_service_revenue_monthly", "idx_erp_create_date"),
    ),
)
def test_migration_rejects_missing_functional_index(
    version: int,
    table: str,
    missing_index: str,
) -> None:
    migration = _discovered_migration(version)
    cursor = ManagedObjectCursor()
    migration.apply(cursor, "warehouse")
    cursor.indexes[table].pop(missing_index)

    assert not migration.is_satisfied(cursor, "warehouse")
    with pytest.raises(RuntimeError, match=r"index.*manual repair"):
        migration.apply(cursor, "warehouse")


@pytest.mark.parametrize("table", ("erp_data", "customer_account"))
def test_auxiliary_migration_rejects_missing_maxvalue_partition(table: str) -> None:
    migration = _discovered_migration(3)
    cursor = ManagedObjectCursor()
    migration.apply(cursor, "warehouse")
    cursor.partitions[table].pop("p_future")

    assert not migration.is_satisfied(cursor, "warehouse")
    with pytest.raises(RuntimeError, match=r"p_future.*MAXVALUE.*manual repair"):
        migration.apply(cursor, "warehouse")


def test_revenue_migration_replaces_same_name_stale_view_with_stable_marker() -> None:
    migration = _discovered_migration(5)
    module = importlib.import_module("work_order_process.migrations.v0005_revenue_summary_objects")
    cursor = ManagedObjectCursor()
    migration.apply(cursor, "warehouse")
    view = "v_ops_service_revenue_monthly_with_total"
    cursor.view_definitions[view] = "SELECT 1 AS sort_order"

    assert not migration.is_satisfied(cursor, "warehouse")
    migration.apply(cursor, "warehouse")

    assert migration.is_satisfied(cursor, "warehouse")
    assert module._VIEW_MARKER in cursor.view_definitions[view]


def test_applied_checksum_drift_is_rejected() -> None:
    cursor = ScriptedMigrationCursor(applied=[(1, "current_schema", "wrong-checksum")])

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


def test_migration_that_remains_unsatisfied_is_not_recorded() -> None:
    connection = MigrationConnection(schema_is_satisfied=False)
    migration = Migration(
        version=1,
        name="incomplete_migration",
        checksum="a" * 64,
        is_satisfied=lambda cursor, database: False,
        apply=lambda cursor, database: None,
    )

    with pytest.raises(SchemaMigrationError, match="not satisfied"):
        apply_pending_migrations(
            connection,
            database="work_order_datalake",
            migrations=(migration,),
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

    assert connection.ddl_statements == ["ALTER TABLE example ADD COLUMN value INT"]
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
            self.result = [(column,) for column in sorted(self.columns.get(table, set()))]
        else:
            self.result = []

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.result

    def fetchone(self) -> tuple[object, ...] | None:
        return self.result[0] if self.result else None


def test_partial_legacy_schema_is_not_mistaken_for_satisfied_baseline() -> None:
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

    assert not v0001_current_schema.is_satisfied(cursor, "warehouse")


def _frozen_v1_columns() -> dict[str, set[str]]:
    columns: dict[str, set[str]] = {}
    ignored = {"PRIMARY", "UNIQUE", "KEY", "INDEX", "PARTITION"}
    for table, statement in v0001_current_schema._TABLE_DDLS:
        table_columns = set()
        for line in statement.splitlines():
            match = re.match(
                r"\s{2}`?([A-Za-z_][A-Za-z0-9_]*)`?\s+",
                line,
            )
            if match and match.group(1).upper() not in ignored:
                table_columns.add(match.group(1))
        columns[table] = table_columns
    for table, alterations in v0001_current_schema._COMPATIBILITY_ALTERS.items():
        columns[table].update(column for column, _ in alterations)
    return columns


@pytest.mark.parametrize(
    ("table", "missing_column"),
    [
        ("ticket_detail_main", "subject"),
        ("ticket_detail_custom_fields", "field_value"),
        ("customers", "customer_name"),
        ("contacts", "contact_name"),
        ("sync_task_log", "task_type"),
        ("customer_history", "customer_name"),
        ("contact_history", "contact_name"),
        ("customer_contact_relation_history", "customer_id"),
        ("api_sync_batch", "status"),
        ("api_raw_record", "payload_json"),
    ],
)
def test_v1_requires_key_write_column_from_every_baseline_table(
    table: str,
    missing_column: str,
) -> None:
    complete_columns = _frozen_v1_columns()
    complete_cursor = InformationSchemaCursor(
        tables=set(complete_columns),
        columns=complete_columns,
    )
    assert v0001_current_schema.is_satisfied(complete_cursor, "warehouse")

    incomplete_columns = {name: set(columns) for name, columns in complete_columns.items()}
    incomplete_columns[table].remove(missing_column)
    incomplete_cursor = InformationSchemaCursor(
        tables=set(incomplete_columns),
        columns=incomplete_columns,
    )

    assert not v0001_current_schema.is_satisfied(
        incomplete_cursor,
        "warehouse",
    )


def test_external_runtime_ddl_cannot_change_frozen_v1_behavior_or_checksum(
    monkeypatch,
) -> None:
    from work_order_process import mysql_storage

    baseline_cursor = InformationSchemaCursor(tables=set(), columns={})
    v0001_current_schema.apply(baseline_cursor, "warehouse")
    baseline_statements = list(baseline_cursor.statements)
    baseline_checksum = discover_migrations()[0].checksum

    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                mysql_storage,
                "TICKET_DETAIL_MAIN_DDL",
                "CREATE TABLE IF NOT EXISTS tampered_runtime_table (id INT)",
            )
            importlib.reload(v0001_current_schema)
            mutated_cursor = InformationSchemaCursor(tables=set(), columns={})
            v0001_current_schema.apply(mutated_cursor, "warehouse")
            mutated_statements = list(mutated_cursor.statements)
            mutated_checksum = discover_migrations()[0].checksum
    finally:
        importlib.reload(v0001_current_schema)

    assert mutated_statements == baseline_statements
    assert mutated_checksum == baseline_checksum


def test_current_schema_apply_executes_base_table_and_compatibility_ddl() -> None:
    cursor = InformationSchemaCursor(tables=set(), columns={})

    v0001_current_schema.apply(cursor, "warehouse")

    statements = [statement for statement, _ in cursor.statements]
    assert sum("CREATE TABLE IF NOT EXISTS" in statement for statement in statements) == 10
    assert any(
        "ALTER TABLE ticket_detail_main" in statement and "ticket_category" in statement
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
        statement for statement, _ in cursor.statements if statement.startswith("ALTER TABLE")
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
    assert status.pending_versions == (1, 2, 3, 4, 5)
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
        lambda config: schema_migrations.SchemaStatus(0, 5, (1, 2, 3, 4, 5), ()),
    )

    with pytest.raises(SchemaMigrationError, match=r"pending.*1.*2"):
        assert_schema_current(_config())


def _settings() -> SimpleNamespace:
    return SimpleNamespace(mysql=_config(), dictionary_path="dictionary.pdf")


def _status(*, pending: tuple[int, ...]) -> object:
    from work_order_process.schema_migrations import SchemaStatus

    return SchemaStatus(
        current_version=0 if pending else 5,
        target_version=5,
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
        lambda config: calls.append(("record", config)) or _status(pending=(2,)),
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
