from collections import deque
from datetime import date, datetime
import logging
from pathlib import Path

import pytest

from work_order_process import customer_account_import
from work_order_process.config import MySQLConfig
from work_order_process.customer_account_import import (
    COLUMN_MAP,
    CustomerAccountImportError,
    _load_stage_rows,
    _publish_staged_snapshot,
    convert_strict,
    import_customer_account_xlsx,
    prepare_customer_account_row,
)


class RecordingCursor:
    def __init__(self, fetchone_values: list[tuple[int]] | None = None) -> None:
        self.executed: list[tuple[str, object | None]] = []
        self.executemany_calls: list[tuple[str, list[list[object]]]] = []
        self._fetchone_values = deque(fetchone_values or [])

    def __enter__(self) -> "RecordingCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: object | None = None) -> None:
        self.executed.append((sql, params))

    def executemany(self, sql: str, rows: list[list[object]]) -> None:
        self.executemany_calls.append((sql, rows))

    def fetchone(self) -> tuple[int]:
        return self._fetchone_values.popleft()


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor | None = None) -> None:
        self.cursor_instance = cursor or RecordingCursor()
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def cursor(self) -> RecordingCursor:
        return self.cursor_instance

    def begin(self) -> None:
        self.begin_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


class RecordingWorkbook:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.sheetnames = ["Sheet1"]
        self._rows = rows
        self.closed = False

    def __getitem__(self, _: str) -> "RecordingWorkbook":
        return self

    def iter_rows(self, *, values_only: bool) -> list[tuple[object, ...]]:
        assert values_only is True
        return self._rows

    def close(self) -> None:
        self.closed = True


def _mysql_config() -> MySQLConfig:
    return MySQLConfig(
        host="fake-host", port=3306, user="fake-user", password="fake-password", database="fake-db"
    )


def test_nonempty_invalid_amount_fails_with_source_row() -> None:
    """A nonempty numeric cell must never silently become NULL."""
    with pytest.raises(
        CustomerAccountImportError,
        match=r"annual_ops_fee.*source row 3",
    ):
        convert_strict("annual_ops_fee", "bad amount", source_row=3)


def test_valid_date_string_is_normalized() -> None:
    """A supported date string has one stable database representation."""
    assert convert_strict("service_expire_date", "2026/07/29", source_row=3) == "2026-07-29"


def test_datetime_and_date_are_normalized() -> None:
    """Excel date values retain their calendar date without a time component."""
    assert convert_strict("ops_start_date", datetime(2026, 7, 29, 14, 30), source_row=3) == "2026-07-29"
    assert convert_strict("ops_end_date", date(2026, 7, 30), source_row=3) == "2026-07-30"


def test_invalid_date_fails_with_column_and_source_row() -> None:
    """An impossible or unsupported nonblank date cannot be published unchanged."""
    with pytest.raises(
        CustomerAccountImportError,
        match=r"service_expire_date.*source row 4",
    ):
        convert_strict("service_expire_date", "2026-02-30", source_row=4)


def test_fractional_integer_fails_with_source_row() -> None:
    """Integer columns must not silently truncate decimal values."""
    with pytest.raises(CustomerAccountImportError, match=r"contract_count.*source row 5"):
        convert_strict("contract_count", "1.5", source_row=5)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_numeric_values_fail_with_source_row(value: float) -> None:
    """MySQL must never receive non-finite spreadsheet numbers."""
    with pytest.raises(CustomerAccountImportError, match=r"annual_ops_fee.*source row 6"):
        convert_strict("annual_ops_fee", value, source_row=6)


def test_empty_customer_names_are_cleaned_not_failed() -> None:
    """Rows without either customer name are intentional business cleaning."""
    values = [None] * len(COLUMN_MAP)

    prepared = prepare_customer_account_row(
        values,
        source_row=2,
        create_date="20260729",
    )

    assert prepared is None


def test_stage_loading_counts_source_accepted_and_cleaned_rows() -> None:
    """Stage validation must use only accepted rows, while retaining source counts."""
    cursor = RecordingCursor(fetchone_values=[(1,)])
    accepted = [None] * len(COLUMN_MAP)
    accepted[1] = "Customer A"

    counts = _load_stage_rows(
        cursor,
        [accepted, [None] * len(COLUMN_MAP)],
        create_date="20260729",
        batch_size=1,
    )

    assert counts == {"rows": 2, "accepted": 1, "inserted": 1, "cleaned": 1}
    assert len(cursor.executemany_calls) == 1
    assert cursor.executemany_calls[0][1][0][-1] == "20260729"


def test_publish_replaces_only_the_selected_snapshot() -> None:
    """Publishing a snapshot must leave every other snapshot untouched."""
    cursor = RecordingCursor(fetchone_values=[(2,)])

    _publish_staged_snapshot(
        cursor,
        create_date="20260729",
        expected_rows=2,
    )

    assert cursor.executed[-3] == (
        "DELETE FROM customer_account WHERE create_date = %s",
        ("20260729",),
    )
    assert "INSERT INTO customer_account" in cursor.executed[-2][0]
    assert cursor.executed[-1] == (
        "SELECT COUNT(*) FROM customer_account WHERE create_date = %s",
        ("20260729",),
    )


def test_publish_count_mismatch_raises_before_commit() -> None:
    """A partial publish must fail before the caller can commit it."""
    cursor = RecordingCursor(fetchone_values=[(1,)])

    with pytest.raises(CustomerAccountImportError, match="expected 2.*published 1"):
        _publish_staged_snapshot(
            cursor,
            create_date="20260729",
            expected_rows=2,
        )


def test_parse_failure_never_touches_formal_snapshot(monkeypatch) -> None:
    """A malformed workbook row must not delete an already-published snapshot."""
    connection = RecordingConnection()
    row = [None] * len(COLUMN_MAP)
    row[1] = "Customer A"
    row[3] = "bad amount"
    workbook = RecordingWorkbook(
        [tuple(header for header, _ in COLUMN_MAP), tuple(row)]
    )
    monkeypatch.setattr(customer_account_import, "load_workbook", lambda *args, **kwargs: workbook)
    monkeypatch.setattr(customer_account_import, "_connect", lambda config: connection)
    monkeypatch.setattr(customer_account_import, "ensure_auxiliary_schema", lambda config: None)

    with pytest.raises(CustomerAccountImportError):
        import_customer_account_xlsx(
            _mysql_config(),
            Path("customer-account.xlsx"),
            "20260729",
        )

    statements = [sql for sql, _ in connection.cursor_instance.executed]
    assert not any(sql.startswith("DELETE FROM customer_account") for sql in statements)
    assert connection.rollback_count == 1


def test_database_failure_summary_hides_workbook_values(monkeypatch, caplog) -> None:
    """Database diagnostics cannot leak raw workbook values through failure summaries."""
    sensitive_cell = "arbitrary-sensitive-cell-token"

    class FailingCursor(RecordingCursor):
        def executemany(self, sql: str, rows: list[list[object]]) -> None:
            raise RuntimeError(
                f"duplicate value {sensitive_cell} user@example.com 13800138000"
            )

    connection = RecordingConnection(FailingCursor())
    row = [None] * len(COLUMN_MAP)
    row[1] = "Customer A"
    workbook = RecordingWorkbook([tuple(header for header, _ in COLUMN_MAP), tuple(row)])
    monkeypatch.setattr(customer_account_import, "load_workbook", lambda *args, **kwargs: workbook)
    monkeypatch.setattr(customer_account_import, "_connect", lambda config: connection)
    monkeypatch.setattr(customer_account_import, "ensure_auxiliary_schema", lambda config: None)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(CustomerAccountImportError):
            import_customer_account_xlsx(
                _mysql_config(),
                Path("customer-account.xlsx"),
                "20260729",
            )

    failure_summary = "\n".join(record.getMessage() for record in caplog.records)
    assert "customer account staging failed" in failure_summary
    assert sensitive_cell not in failure_summary
    assert "user@example.com" not in failure_summary
    assert "13800138000" not in failure_summary
