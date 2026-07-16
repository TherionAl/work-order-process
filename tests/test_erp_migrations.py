from __future__ import annotations

from dataclasses import dataclass, field

from work_order_process.config import MySQLConfig
from work_order_process.erp_migrations import ensure_erp_allocation_columns


ALLOCATION_COLUMNS = {
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


@dataclass
class FakeCursor:
    existing_columns: set[str]
    statements: list[tuple[str, object]] = field(default_factory=list)

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: object = None) -> None:
        self.statements.append((statement, params))

    def fetchall(self) -> list[tuple[str]]:
        return [(column,) for column in self.existing_columns]


@dataclass
class FakeConnection:
    cursor_instance: FakeCursor

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


class FakePyMySQL:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.connect_calls: list[dict[str, object]] = []

    def connect(self, **kwargs: object) -> FakeConnection:
        self.connect_calls.append(kwargs)
        return self.connection


def _config() -> MySQLConfig:
    return MySQLConfig(host="db.example", port=3306, user="user", password="secret", database="warehouse")


def _install_fake_pymysql(monkeypatch, existing_columns: set[str]) -> FakeCursor:
    from work_order_process import erp_migrations

    cursor = FakeCursor(existing_columns)
    monkeypatch.setattr(erp_migrations, "pymysql", FakePyMySQL(FakeConnection(cursor)))
    return cursor


def test_adds_all_missing_allocation_columns_with_expected_definitions(monkeypatch) -> None:
    cursor = _install_fake_pymysql(monkeypatch, set())

    added = ensure_erp_allocation_columns(_config())

    alters = [statement for statement, _ in cursor.statements if statement.startswith("ALTER TABLE")]
    assert added == list(ALLOCATION_COLUMNS)
    assert len(alters) == 9
    for column, definition in ALLOCATION_COLUMNS.items():
        assert f"ALTER TABLE erp_data ADD COLUMN {column} {definition}" in alters


def test_returns_empty_list_without_alter_when_allocation_columns_exist(monkeypatch) -> None:
    cursor = _install_fake_pymysql(monkeypatch, set(ALLOCATION_COLUMNS))

    assert ensure_erp_allocation_columns(_config()) == []
    assert not [statement for statement, _ in cursor.statements if statement.startswith("ALTER TABLE")]


def test_adds_only_missing_allocation_columns(monkeypatch) -> None:
    existing = set(ALLOCATION_COLUMNS) - {"contract_days", "cur_year_adjusted_amort"}
    cursor = _install_fake_pymysql(monkeypatch, existing)

    assert ensure_erp_allocation_columns(_config()) == ["contract_days", "cur_year_adjusted_amort"]
    alters = [statement for statement, _ in cursor.statements if statement.startswith("ALTER TABLE")]
    assert alters == [
        "ALTER TABLE erp_data ADD COLUMN contract_days INT NULL COMMENT '合同天数'",
        "ALTER TABLE erp_data ADD COLUMN cur_year_adjusted_amort DECIMAL(18,2) NULL COMMENT '今年倒签调整后分摊服务费'",
    ]


def test_ensure_auxiliary_schema_runs_allocation_migration(monkeypatch) -> None:
    from work_order_process import auxiliary_schema

    calls: list[MySQLConfig] = []
    monkeypatch.setattr(auxiliary_schema, "ensure_erp_allocation_columns", calls.append)
    monkeypatch.setattr(auxiliary_schema, "pymysql", FakePyMySQL(FakeConnection(FakeCursor(set()))))

    auxiliary_schema.ensure_auxiliary_schema(_config())

    assert calls == [_config()]
