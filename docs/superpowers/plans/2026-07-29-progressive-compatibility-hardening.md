# Progressive Compatibility Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make snapshot imports atomic and diagnosable, add controlled HTTP retry and explicit schema migrations, split oversized modules behind compatibility layers, and enforce lint, formatting, and 70% test coverage.

**Architecture:** Introduce small core modules for failures, HTTP transport, migrations, sync logging, ticket import, and CLI handlers. Existing public modules remain compatibility facades, so current Python imports, CLI commands, and production entry points continue to work while responsibilities move incrementally.

**Tech Stack:** Python 3.14, PyMySQL, httpx, openpyxl, argparse, pytest, pytest-cov, coverage.py, Ruff, uv, MySQL 8

## Global Constraints

- Preserve all existing `work_order_process.mysql_storage` public functions, DDL constants, and types.
- Preserve `work_order_process.api.WorkOrderClient` construction and public methods.
- Preserve `uv run work_order_process ...`, `uv run erp-merge ...`, and `python -m work_order_process.daily_runner`.
- Do not connect to or mutate the production database or server.
- Do not scan, modify, stage, or commit `data/` or the user's existing untracked files.
- Do not add a strict business uniqueness constraint to `customer_account`.
- Do not store credentials, full payloads, phone numbers, or email addresses in failure summaries.
- Write and run a failing test before every production behavior change.
- Keep each task in a separate, narrowly scoped commit.
- Keep the coverage denominator honest: do not omit low-coverage application modules from coverage configuration.
- The approved design is `docs/superpowers/specs/2026-07-29-progressive-compatibility-hardening-design.md`.

## Baseline

Before implementation, the verified baseline is:

```text
uv run --all-groups pytest -q
159 passed

uv run --with pytest-cov pytest --cov=work_order_process --cov-report=term -q
TOTAL 4004 statements, 1820 missed, 55% covered
```

The largest initial coverage gaps are `api.py` 35%, `cli.py` 28%,
`customer_account_import.py` 24%, `customer_contact_sync.py` 45%,
`monthly_export.py` 40%, `mysql_storage.py` 21%, `personnel_import.py` 33%,
and `time_metrics.py` 39%.

---

### Task 1: Safe Structured Import Failures

**Files:**
- Create: `src/work_order_process/import_failures.py`
- Create: `tests/test_import_failures.py`

**Interfaces:**
- Produces: `ImportFailure`
- Produces: `FailureCollector`
- Produces: `sanitize_failure_message(exc: BaseException, *, secrets: Iterable[str] = ()) -> str`
- Produces: `FailureCollector.capture(*, stage: str, exc: BaseException, record_id: object | None = None, source_row: int | None = None, secrets: Iterable[str] = ()) -> ImportFailure`
- Produces: `FailureCollector.as_payload() -> dict[str, object]`

- [ ] **Step 1: Write tests for bounded collection and redaction**

```python
from work_order_process.import_failures import FailureCollector


def test_failure_collector_redacts_sensitive_values_and_limits_details() -> None:
    collector = FailureCollector(limit=2)
    collector.capture(
        stage="parse",
        source_row=2,
        exc=ValueError(
            "password=secret-value email=user@example.com phone=13800138000"
        ),
        secrets=("secret-value",),
    )
    collector.capture(stage="database", record_id="T2", exc=RuntimeError("deadlock"))
    collector.capture(stage="api", record_id="T3", exc=RuntimeError("timeout"))

    payload = collector.as_payload()

    assert payload["failure_count"] == 3
    assert payload["failures_truncated"] is True
    assert len(payload["failures"]) == 2
    serialized = repr(payload)
    assert "secret-value" not in serialized
    assert "user@example.com" not in serialized
    assert "13800138000" not in serialized


def test_failure_requires_record_id_or_source_row() -> None:
    collector = FailureCollector()

    with pytest.raises(ValueError, match="record_id or source_row"):
        collector.capture(stage="parse", exc=ValueError("bad"))
```

- [ ] **Step 2: Run the tests and verify the expected import failure**

Run:

```powershell
uv run --all-groups pytest tests/test_import_failures.py -q
```

Expected: collection fails because `work_order_process.import_failures` does not exist.

- [ ] **Step 3: Implement the immutable failure record and collector**

```python
@dataclass(frozen=True)
class ImportFailure:
    stage: str
    error_type: str
    safe_message: str
    record_id: str | None = None
    source_row: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "record_id": self.record_id,
            "source_row": self.source_row,
            "error_type": self.error_type,
            "safe_message": self.safe_message,
        }


class FailureCollector:
    def __init__(self, limit: int = 100) -> None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        self.limit = limit
        self.total = 0
        self.failures: list[ImportFailure] = []

    def capture(
        self,
        *,
        stage: str,
        exc: BaseException,
        record_id: object | None = None,
        source_row: int | None = None,
        secrets: Iterable[str] = (),
    ) -> ImportFailure:
        if record_id is None and source_row is None:
            raise ValueError("record_id or source_row is required")
        failure = ImportFailure(
            stage=stage,
            record_id=None if record_id is None else str(record_id),
            source_row=source_row,
            error_type=type(exc).__name__,
            safe_message=sanitize_failure_message(exc, secrets=secrets),
        )
        self.total += 1
        if len(self.failures) < self.limit:
            self.failures.append(failure)
        return failure

    def as_payload(self) -> dict[str, object]:
        return {
            "failure_count": self.total,
            "failures": [failure.as_dict() for failure in self.failures],
            "failures_truncated": self.total > len(self.failures),
        }
```

`sanitize_failure_message` must replace:

```text
explicit secret values -> [redacted]
email addresses         -> [email]
Chinese mobile numbers  -> [phone]
password assignments    -> password=[redacted]
```

Limit the final message to 500 characters after redaction.

- [ ] **Step 4: Run focused and full tests**

```powershell
uv run --all-groups pytest tests/test_import_failures.py -q
uv run --all-groups pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the failure model**

```powershell
git add src/work_order_process/import_failures.py tests/test_import_failures.py
git diff --cached --check
git commit -m "feat: add safe structured import failures"
```

---

### Task 2: Atomic and Idempotent Customer-Account Snapshots

**Files:**
- Modify: `src/work_order_process/customer_account_import.py`
- Create: `tests/test_customer_account_import.py`
- Modify: `tests/test_snapshot_imports.py`

**Interfaces:**
- Consumes: `ImportFailure`, `FailureCollector`
- Produces: `CustomerAccountImportError(RuntimeError)`
- Produces: `convert_strict(column: str, value: object, *, source_row: int) -> object`
- Produces: `prepare_customer_account_row(values: Sequence[object], *, source_row: int, create_date: str) -> list[object] | None`
- Produces: `_load_stage_rows(cursor: Any, rows: Iterable[Sequence[object]], *, create_date: str, batch_size: int) -> dict[str, int]`
- Produces: `_publish_staged_snapshot(cursor: Any, *, create_date: str, expected_rows: int) -> None`
- Preserves: `convert(column: str, value: object) -> object`
- Preserves: `import_customer_account_xlsx(...) -> dict`

- [ ] **Step 1: Write strict conversion and cleaning tests**

```python
def test_nonempty_invalid_amount_fails_with_source_row() -> None:
    with pytest.raises(
        CustomerAccountImportError,
        match=r"annual_ops_fee.*第 3 行",
    ):
        convert_strict("annual_ops_fee", "bad amount", source_row=3)


def test_empty_customer_names_are_cleaned_not_failed() -> None:
    values = [None] * len(COLUMN_MAP)

    prepared = prepare_customer_account_row(
        values,
        source_row=2,
        create_date="20260729",
    )

    assert prepared is None
```

The production change that makes these tests pass is strict nonempty parsing plus an explicit `None` result for the existing business-cleaning rule.

- [ ] **Step 2: Run the strict conversion tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_customer_account_import.py -q
```

Expected: failure because the strict interfaces do not exist.

- [ ] **Step 3: Implement strict converters without changing legacy `convert`**

Use dedicated strict converters that raise `CustomerAccountImportError` for nonempty invalid values. The existing `convert` function keeps its permissive behavior for backward compatibility and existing mapping tests.

```python
def convert_strict(column: str, value: object, *, source_row: int) -> object:
    converter = STRICT_CONVERTERS.get(column)
    if converter is None:
        return _to_str(value)
    try:
        return converter(value)
    except (TypeError, ValueError) as exc:
        raise CustomerAccountImportError(
            f"{column} 非空值无法解析，源数据第 {source_row} 行"
        ) from exc
```

Strict numeric converters must distinguish blank values from invalid values; blank remains `None`.

- [ ] **Step 4: Run strict conversion tests**

```powershell
uv run --all-groups pytest tests/test_customer_account_import.py -q
```

Expected: strict conversion and cleaning tests pass.

- [ ] **Step 5: Write staging and publish tests**

```python
def test_publish_replaces_only_the_selected_snapshot() -> None:
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
    cursor = RecordingCursor(fetchone_values=[(1,)])

    with pytest.raises(CustomerAccountImportError, match="expected 2.*published 1"):
        _publish_staged_snapshot(
            cursor,
            create_date="20260729",
            expected_rows=2,
        )
```

- [ ] **Step 6: Run publish tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_customer_account_import.py -q
```

Expected: failure because staging SQL and publish validation are not implemented.

- [ ] **Step 7: Implement stage loading and transactional publish**

Create constants from `COLUMN_MAP`:

```python
IMPORT_COLUMNS = tuple(column for _, column in COLUMN_MAP) + ("create_date",)
STAGE_TABLE = "customer_account_import_stage"
CREATE_STAGE_SQL = (
    f"CREATE TEMPORARY TABLE {STAGE_TABLE} AS "
    f"SELECT {', '.join(IMPORT_COLUMNS)} FROM customer_account WHERE 1 = 0"
)
STAGE_INSERT_SQL = (
    f"INSERT INTO {STAGE_TABLE} ({', '.join(IMPORT_COLUMNS)}) VALUES "
    f"({', '.join(['%s'] * len(IMPORT_COLUMNS))})"
)
```

`_load_stage_rows` must:

1. Create the temporary table.
2. Enumerate workbook data starting at source row 2.
3. Call `prepare_customer_account_row`.
4. Count source, accepted, and cleaned rows separately.
5. Use `executemany` in `batch_size` chunks.
6. Validate `SELECT COUNT(*) FROM stage` equals accepted rows.

`import_customer_account_xlsx` must load and validate the stage before
`connection.begin()`. The formal `DELETE`, publish `INSERT ... SELECT`, count
check, and commit must occur after `begin()` in one transaction. Any exception
must call rollback and raise `CustomerAccountImportError`; no database exception
may be converted to `skipped`.

- [ ] **Step 8: Add an orchestration test proving parse failure never deletes the old snapshot**

```python
def test_parse_failure_never_touches_formal_snapshot(monkeypatch, workbook) -> None:
    connection = RecordingConnection()
    workbook.add_row(annual_ops_fee="bad amount")
    monkeypatch.setattr(customer_account_import, "load_workbook", lambda *a, **k: workbook)
    monkeypatch.setattr(customer_account_import, "_connect", lambda config: connection)

    with pytest.raises(CustomerAccountImportError):
        import_customer_account_xlsx(
            _mysql_config(),
            Path("customer-account.xlsx"),
            "20260729",
        )

    statements = [sql for sql, _ in connection.cursor_instance.executed]
    assert not any(
        sql.startswith("DELETE FROM customer_account")
        for sql in statements
    )
    assert connection.rollback_count == 1
```

- [ ] **Step 9: Run customer-account and snapshot tests**

```powershell
uv run --all-groups pytest tests/test_customer_account_import.py tests/test_snapshot_imports.py -q
uv run --all-groups pytest -q
```

Expected: all tests pass and the report contains `rows`, `accepted`, `inserted`,
`cleaned`, `failed`, `seconds`, and `create_date`.

- [ ] **Step 10: Commit the atomic snapshot importer**

```powershell
git add src/work_order_process/customer_account_import.py tests/test_customer_account_import.py tests/test_snapshot_imports.py
git diff --cached --check
git commit -m "fix: publish customer account snapshots atomically"
```

---

### Task 3: Failure Details in Entity and Ticket Sync

**Files:**
- Modify: `src/work_order_process/customer_contact_sync.py`
- Modify: `src/work_order_process/mysql_storage.py`
- Modify: `src/work_order_process/customer_account_import.py`
- Modify: `tests/test_customer_contact_sync.py`
- Modify: `tests/test_mysql_storage.py`
- Modify: `tests/test_customer_account_import.py`

**Interfaces:**
- Consumes: `FailureCollector`
- Changes: `SyncReport` adds `failures: tuple[dict[str, object], ...] = ()`
- Changes: `SyncReport` adds `failures_truncated: bool = False`
- Changes: ticket import reports preserve `failed_ids` and add `failures` plus `failures_truncated`
- Changes: customer-account failure exceptions expose `failure: ImportFailure`

- [ ] **Step 1: Write entity-sync failure classification tests**

```python
class InvalidRecordClient:
    def iter_companies(self):
        yield [{"uId": "", "companyName": "missing stable id"}]


def test_invalid_customer_is_failed_with_safe_reason() -> None:
    store = FakeStore()

    report = sync_customer_entities(
        None,
        InvalidRecordClient(),
        sources=["companies"],
        store=store,
    )

    assert report.status == "partial"
    assert report.failed == 1
    assert report.failures[0]["stage"] == "prepare"
    assert report.failures[0]["source_row"] == 1
    assert report.failures[0]["error_type"]


class BulkFailsThenOneRowFailsStore(FakeStore):
    def save_entities(self, **_: object) -> dict[str, int]:
        raise RuntimeError("bulk write failed")

    def save_entity(self, **kwargs: object) -> str:
        if kwargs["row"]["customer_id"] == "C2":
            raise RuntimeError("row write failed")
        return "inserted"


def test_bulk_fallback_records_only_final_row_failure() -> None:
    report = sync_customer_entities(
        None,
        PagedClient(),
        sources=["companies"],
        store=BulkFailsThenOneRowFailsStore(),
    )

    assert report.inserted == 2
    assert report.failed == 1
    assert [failure["record_id"] for failure in report.failures] == ["C2"]
    assert report.failures[0]["stage"] == "database"
```

- [ ] **Step 2: Run entity-sync tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_customer_contact_sync.py -q
```

Expected: failures because `SyncReport` has no failure details and exceptions are swallowed.

- [ ] **Step 3: Integrate `FailureCollector` into entity preparation and fallback**

Use `enumerate(..., start=1)` for stable per-batch source positions. When bulk
write fails, attempt each row as today; only capture the rows that still fail.
Pass `failure_payload` to `_finish_batch` so `api_sync_batch.error_message`
contains a safe one-line summary while detailed safe records remain in the
returned report.

The outer exception path must capture the exception with `record_id=batch_id`
instead of storing only `type(exc).__name__`.

- [ ] **Step 4: Write ticket-detail API and database failure tests**

```python
def test_fetch_batch_details_returns_api_failure_reason() -> None:
    client = FailingTicketClient(RuntimeError("temporary API failure"))

    details, failures = _fetch_batch_details(
        client,
        ["T1"],
        FakeResolver(),
        threading.Semaphore(1),
        max_workers=1,
    )

    assert details == {}
    assert failures.as_payload()["failures"][0]["record_id"] == "T1"
    assert failures.as_payload()["failures"][0]["stage"] == "api"


def test_commit_batch_returns_database_failure_reason() -> None:
    connection = RowFailingConnection(ticket_id="T2")

    report = _commit_batch(connection, _detail_map("T1", "T2"))

    assert report["failed_ids"] == ["T2"]
    assert report["failures"][0]["record_id"] == "T2"
    assert report["failures"][0]["stage"] == "database"
```

- [ ] **Step 5: Run ticket failure tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_mysql_storage.py -q
```

Expected: failure because the helpers currently return details or IDs without structured reasons.

- [ ] **Step 6: Return and persist ticket failure payloads**

Change internal `_fetch_batch_details` to return:

```python
tuple[
    dict[str, tuple[dict[str, Any], dict[str, Any]]],
    FailureCollector,
]
```

Change `_commit_batch` and `_commit_batch_atomic` reports to include:

```python
{
    "failed_ids": [...],
    "failures": [...],
    "failures_truncated": bool,
}
```

Merge API and database collectors in `import_month_tickets_to_mysql`. Keep
`failed_ids` for compatibility. Store the bounded failure list under
`sync_task_log.extra_json["failures"]`; do not store exception payloads.

- [ ] **Step 7: Attach a structured failure to customer-account errors**

`CustomerAccountImportError` must accept and expose an `ImportFailure`. Tests
must assert:

```python
assert error.value.failure.stage == "parse"
assert error.value.failure.source_row == 3
```

- [ ] **Step 8: Run focused and full tests**

```powershell
uv run --all-groups pytest tests/test_import_failures.py tests/test_customer_account_import.py tests/test_customer_contact_sync.py tests/test_mysql_storage.py -q
uv run --all-groups pytest -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit failure integration**

```powershell
git add src/work_order_process/customer_account_import.py src/work_order_process/customer_contact_sync.py src/work_order_process/mysql_storage.py tests/test_customer_account_import.py tests/test_customer_contact_sync.py tests/test_mysql_storage.py
git diff --cached --check
git commit -m "fix: preserve import failure reasons"
```

---

### Task 4: Controlled HTTP Retry Transport

**Files:**
- Create: `src/work_order_process/api_transport.py`
- Create: `tests/test_api_transport.py`
- Modify: `src/work_order_process/api.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: `RetryPolicy`
- Produces: `request_with_retry(client: httpx.Client, method: str, path: str, data: dict[str, Any], *, policy: RetryPolicy = DEFAULT_RETRY_POLICY, sleep: Callable[[float], None] = time.sleep, random_value: Callable[[], float] = random.random) -> httpx.Response`
- Produces: `retry_delay(response: httpx.Response | None, attempt: int, policy: RetryPolicy, random_value: Callable[[], float]) -> float`
- Preserves: `WorkOrderClient._request(method, path, data) -> httpx.Response`

- [ ] **Step 1: Write retry behavior tests**

```python
def test_429_uses_retry_after_then_succeeds() -> None:
    client = ScriptedHTTPClient(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    waits: list[float] = []

    response = request_with_retry(
        client,
        "GET",
        "/tickets",
        {"page": 1},
        sleep=waits.append,
        random_value=lambda: 0.0,
    )

    assert response.status_code == 200
    assert waits == [2.0]
    assert client.calls == 2


@pytest.mark.parametrize("status", [401, 403, 404])
def test_permanent_client_errors_are_not_retried(status: int) -> None:
    client = ScriptedHTTPClient([httpx.Response(status)])

    response = request_with_retry(
        client,
        "GET",
        "/tickets",
        {},
        sleep=lambda _: None,
    )

    assert response.status_code == status
    assert client.calls == 1


@pytest.mark.parametrize("status", [502, 503, 504])
def test_transient_server_errors_are_retried(status: int) -> None:
    client = ScriptedHTTPClient(
        [httpx.Response(status), httpx.Response(200)]
    )

    assert request_with_retry(
        client,
        "GET",
        "/tickets",
        {},
        sleep=lambda _: None,
        random_value=lambda: 0.0,
    ).status_code == 200
    assert client.calls == 2


def test_transport_error_stops_after_three_attempts() -> None:
    client = ScriptedHTTPClient(
        [
            httpx.ConnectError("down"),
            httpx.ConnectError("down"),
            httpx.ConnectError("down"),
        ]
    )

    with pytest.raises(httpx.ConnectError):
        request_with_retry(client, "GET", "/tickets", {}, sleep=lambda _: None)

    assert client.calls == 3
```

- [ ] **Step 2: Run transport tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_api_transport.py -q
```

Expected: failure because the transport module does not exist.

- [ ] **Step 3: Implement the retry policy**

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retry_statuses: frozenset[int] = frozenset({429, 502, 503, 504})
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.25
```

`request_with_retry` must:

1. Reject methods other than `GET` and `POST` with `ApiTransportError`.
2. Send GET data as `params` and POST data as form `data`.
3. Return immediately for success and nonretryable HTTP responses.
4. Retry only transport exceptions and configured statuses.
5. Sleep only between attempts.
6. Re-raise the final transport exception unchanged.

- [ ] **Step 4: Delegate `WorkOrderClient._request` to the transport**

Replace the inline loop in `api.py` with a single call to
`request_with_retry`. Add a compatibility test:

```python
def test_work_order_client_request_delegates_to_transport(monkeypatch) -> None:
    client = WorkOrderClient(_settings())
    expected = httpx.Response(200, json={"ok": True})
    calls = []
    monkeypatch.setattr(
        api,
        "request_with_retry",
        lambda http, method, path, data: calls.append(
            (http, method, path, data)
        ) or expected,
    )

    assert client._request("GET", "/tickets", {"page": 1}) is expected
    assert calls == [(client.client, "GET", "/tickets", {"page": 1})]
```

- [ ] **Step 5: Run API tests and full suite**

```powershell
uv run --all-groups pytest tests/test_api_transport.py tests/test_api.py -q
uv run --all-groups pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit HTTP transport**

```powershell
git add src/work_order_process/api_transport.py src/work_order_process/api.py tests/test_api_transport.py tests/test_api.py
git diff --cached --check
git commit -m "feat: retry transient work order API failures"
```

---

### Task 5: Explicit Versioned Schema Migrations

**Files:**
- Create: `src/work_order_process/schema_migrations.py`
- Create: `src/work_order_process/migrations/__init__.py`
- Create: `src/work_order_process/migrations/v0001_current_schema.py`
- Create: `src/work_order_process/migrations/v0002_erp_allocation_columns.py`
- Create: `tests/test_schema_migrations.py`
- Modify: `src/work_order_process/erp_migrations.py`
- Modify: `src/work_order_process/auxiliary_schema.py`
- Modify: `src/work_order_process/cli.py`
- Modify: `src/work_order_process/daily_runner.py`
- Modify: `tests/test_erp_migrations.py`
- Modify: `tests/test_snapshot_imports.py`
- Modify: `tests/test_daily_runner.py`

**Interfaces:**
- Produces: `Migration`
- Produces: `SchemaStatus`
- Produces: `SchemaMigrationError(RuntimeError)`
- Produces: `discover_migrations() -> tuple[Migration, ...]`
- Produces: `schema_status(config: MySQLConfig) -> SchemaStatus`
- Produces: `migrate_schema(config: MySQLConfig) -> SchemaStatus`
- Produces: `assert_schema_current(config: MySQLConfig) -> None`
- Produces CLI: `mysql-schema-status`
- Produces CLI: `mysql-migrate`
- Preserves: `ensure_erp_allocation_columns(config) -> list[str]`

- [ ] **Step 1: Write migration discovery and checksum tests**

```python
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
```

- [ ] **Step 2: Run discovery tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_schema_migrations.py -q
```

Expected: failure because migrations do not exist.

- [ ] **Step 3: Implement migration metadata and discovery**

```python
@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    is_satisfied: Callable[[Any, str], bool]
    apply: Callable[[Any, str], None]


@dataclass(frozen=True)
class SchemaStatus:
    current_version: int
    target_version: int
    pending_versions: tuple[int, ...]
    drifted_versions: tuple[int, ...]

    @property
    def is_current(self) -> bool:
        return not self.pending_versions and not self.drifted_versions
```

Each migration module exports `VERSION`, `NAME`, `is_satisfied`, and `apply`.
Compute SHA-256 from the migration module file bytes. Validate strictly
increasing unique versions at discovery.

- [ ] **Step 4: Write status and apply tests**

```python
def test_satisfied_existing_schema_is_recorded_without_running_ddl() -> None:
    connection = MigrationConnection(schema_is_satisfied=True)

    status = apply_pending_migrations(
        connection,
        database="work_order_datalake",
        migrations=(_migration(1),),
    )

    assert connection.ddl_statements == []
    assert connection.recorded_versions == [1]
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
```

- [ ] **Step 5: Implement `schema_version`, status, and explicit apply**

Create `SCHEMA_VERSION_DDL` with:

```sql
CREATE TABLE IF NOT EXISTS schema_version (
  version INT NOT NULL,
  name VARCHAR(200) NOT NULL,
  checksum CHAR(64) NOT NULL,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

For each pending migration:

1. If `is_satisfied` is true, record the version without DDL.
2. Otherwise invoke `apply`.
3. Record the version only after `apply` returns successfully.
4. Commit the version record.
5. On failure, rollback the connection and raise `SchemaMigrationError`.

Document in the exception that MySQL DDL can auto-commit; a rerun uses
`is_satisfied` to reconcile a completed DDL statement whose version record was
not written.

- [ ] **Step 6: Move ERP allocation migration behavior**

`v0002_erp_allocation_columns.py` owns the column definitions and adds only
columns missing from `information_schema.columns`.

`erp_migrations.ensure_erp_allocation_columns` becomes a compatibility wrapper
that invokes only the migration's `is_satisfied`/`apply` behavior when called
explicitly. Remove the call from `ensure_auxiliary_schema`; ordinary import
must no longer alter populated tables.

- [ ] **Step 7: Write CLI and daily-runner preflight tests**

```python
def test_schema_status_command_is_read_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "schema_status", lambda config: _status(pending=(2,)))
    monkeypatch.setattr(sys, "argv", ["work_order_process", "mysql-schema-status"])

    cli.main()

    assert "pending" in capsys.readouterr().out.lower()


def test_daily_runner_refuses_outdated_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        daily_runner,
        "assert_schema_current",
        Mock(side_effect=SchemaMigrationError("pending migration 2")),
    )

    with pytest.raises(SchemaMigrationError, match="pending migration 2"):
        daily_runner.main()
```

- [ ] **Step 8: Add `mysql-schema-status` and `mysql-migrate`**

Add both commands to the existing positional command choices. `mysql-migrate`
prints applied and remaining versions. All other database-mutating CLI commands
call `assert_schema_current` before work. `mysql-init` creates the current base
tables and records migrations whose `is_satisfied` checks pass.

- [ ] **Step 9: Run migration, CLI, scheduler, and full tests**

```powershell
uv run --all-groups pytest tests/test_schema_migrations.py tests/test_erp_migrations.py tests/test_snapshot_imports.py tests/test_daily_runner.py -q
uv run --all-groups pytest -q
```

Expected: all tests pass without a real database connection.

- [ ] **Step 10: Commit explicit migrations**

```powershell
git add src/work_order_process/schema_migrations.py src/work_order_process/migrations src/work_order_process/erp_migrations.py src/work_order_process/auxiliary_schema.py src/work_order_process/cli.py src/work_order_process/daily_runner.py tests/test_schema_migrations.py tests/test_erp_migrations.py tests/test_snapshot_imports.py tests/test_daily_runner.py
git diff --cached --check
git commit -m "feat: add explicit versioned schema migrations"
```

---

### Task 6: Extract Sync Logging and Ticket Import Behind Compatibility Facades

**Files:**
- Create: `src/work_order_process/sync_log.py`
- Create: `src/work_order_process/ticket_import.py`
- Create: `tests/test_sync_log.py`
- Create: `tests/test_ticket_import.py`
- Modify: `src/work_order_process/mysql_storage.py`
- Modify: `tests/test_mysql_storage.py`

**Interfaces:**
- Produces: `write_sync_log(...) -> None`
- Produces: `read_sync_logs(config: MySQLConfig, limit: int) -> list[dict[str, Any]]`
- Moves: `import_month_tickets_serial`
- Moves: `import_month_tickets_to_mysql`
- Moves: `import_year_tickets_to_mysql`
- Moves internal batch fetch/commit orchestration used only by these functions
- Preserves all old `mysql_storage` imports through explicit wrappers or aliases

- [ ] **Step 1: Write compatibility identity and behavior tests**

```python
def test_mysql_storage_reexports_sync_log_ddl() -> None:
    assert mysql_storage.SYNC_TASK_LOG_DDL == sync_log.SYNC_TASK_LOG_DDL


def test_legacy_month_import_calls_new_implementation(monkeypatch) -> None:
    sentinel = {"month": "2026-07"}
    monkeypatch.setattr(
        ticket_import,
        "import_month_tickets_to_mysql",
        lambda *args, **kwargs: sentinel,
    )

    assert mysql_storage.import_month_tickets_to_mysql(
        _config(),
        _dictionary(),
        _client(),
        2026,
        7,
    ) is sentinel
```

- [ ] **Step 2: Run compatibility tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_sync_log.py tests/test_ticket_import.py -q
```

Expected: failure because extracted modules do not exist.

- [ ] **Step 3: Extract `sync_log.py`**

Move `SYNC_TASK_LOG_DDL` and `_write_sync_log` behavior to `sync_log.py`.
Rename the public implementation `write_sync_log`. Add `read_sync_logs` with a
bounded positive `limit`. `mysql_storage.py` imports `SYNC_TASK_LOG_DDL` and
provides:

```python
def _write_sync_log(*args: Any, **kwargs: Any) -> None:
    write_sync_log(*args, **kwargs)
```

The new module imports PyMySQL directly and must not import `mysql_storage`.

- [ ] **Step 4: Extract ticket orchestration**

Move month/year orchestration plus its private batch helpers into
`ticket_import.py`. The new module may import stable persistence primitives
from `mysql_storage`, including `ensure_mysql_schema`,
`build_ticket_detail_main_row`, `build_ticket_detail_custom_field_rows`, and
`_upsert_ticket_detail`.

Avoid import cycles by making legacy wrappers import implementations inside the
function body:

```python
def import_month_tickets_to_mysql(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    max_workers: int = 8,
    batch_size: int = 100,
    api_rate_limit: int = 10,
) -> dict[str, Any]:
    from .ticket_import import import_month_tickets_to_mysql as implementation

    return implementation(
        config,
        dictionary,
        client,
        year,
        month,
        per_page=per_page,
        limit_per_month=limit_per_month,
        max_workers=max_workers,
        batch_size=batch_size,
        api_rate_limit=api_rate_limit,
    )
```

Use explicit signatures for every compatibility wrapper; do not use a generic
`*args, **kwargs` facade for public import functions.

- [ ] **Step 5: Add direct tests for empty month, current-only month, mixed batch, and year aggregation**

`tests/test_ticket_import.py` must directly cover:

```text
empty API month -> zero report, no write
all rows current -> skipped count and success sync log
API missing detail plus database row failure -> two structured failures
year import -> twelve ordered month reports and aggregate totals
```

Use fake client, fake persistence functions, and fake sync-log writer; no MySQL
connection.

- [ ] **Step 6: Run old-path and new-path tests**

```powershell
uv run --all-groups pytest tests/test_sync_log.py tests/test_ticket_import.py tests/test_mysql_storage.py tests/test_daily_runner.py -q
uv run --all-groups pytest -q
```

Expected: all tests pass and existing callers need no import changes.

- [ ] **Step 7: Commit the compatibility split**

```powershell
git add src/work_order_process/sync_log.py src/work_order_process/ticket_import.py src/work_order_process/mysql_storage.py tests/test_sync_log.py tests/test_ticket_import.py tests/test_mysql_storage.py
git diff --cached --check
git commit -m "refactor: split ticket import and sync logging"
```

---

### Task 7: Split CLI Handlers and Correct Defaults

**Files:**
- Create: `src/work_order_process/cli_commands/__init__.py`
- Create: `src/work_order_process/cli_commands/database.py`
- Create: `src/work_order_process/cli_commands/imports.py`
- Create: `src/work_order_process/cli_commands/exports.py`
- Create: `src/work_order_process/cli_commands/diagnostics.py`
- Create: `tests/test_cli.py`
- Modify: `src/work_order_process/cli.py`
- Modify: `tests/test_handover_guide.py`

**Interfaces:**
- Produces: `build_parser() -> argparse.ArgumentParser`
- Produces: `dispatch_command(args: argparse.Namespace, settings: Settings, parser: argparse.ArgumentParser) -> None`
- Preserves: `main() -> None`
- Corrects: `--customers-source` default/help to `companies`
- Corrects: `--contacts-source` default/help to `contacts`
- Corrects: `--personnel-file` default to `None`

- [ ] **Step 1: Write parser-default and personnel validation tests**

```python
def test_parser_defaults_match_help_text() -> None:
    parser = build_parser()
    args = parser.parse_args(["mysql-import-customers"])

    assert args.customers_source == "companies"
    assert args.contacts_source == "contacts"
    help_text = parser.format_help()
    assert "客户导入的数据源，默认 companies" in help_text
    assert "联系人导入的数据源，默认 contacts" in help_text


def test_personnel_import_requires_explicit_file(capsys) -> None:
    parser = build_parser()
    args = parser.parse_args(["mysql-import-personnel"])

    with pytest.raises(SystemExit, match="2"):
        dispatch_command(args, _settings(), parser)

    assert "--personnel-file" in capsys.readouterr().err
```

- [ ] **Step 2: Run CLI tests and verify failure**

```powershell
uv run --all-groups pytest tests/test_cli.py -q
```

Expected: failure because `build_parser` and `dispatch_command` are not public
and current defaults/help disagree.

- [ ] **Step 3: Extract `build_parser` and correct defaults**

Move parser construction out of `main`. Keep the positional command and every
existing option name. Set:

```python
parser.add_argument(
    "--personnel-file",
    default=None,
    help="mysql-import-personnel: required personnel .xls file path.",
)
```

Use `parser.error("mysql-import-personnel requires --personnel-file")` before
calling the importer.

- [ ] **Step 4: Extract domain handlers**

Move command execution into:

```text
database.py    mysql-init, migrations, partitions, sync logs, analysis views
imports.py     ticket, customer, contact, personnel, ERP, customer account
exports.py     monthly exports, template samples, revenue, time metrics
diagnostics.py probe, dictionary, read-only entity probes
```

Each module exposes `COMMANDS: frozenset[str]` and
`handle(args, settings, parser) -> bool`. It returns `True` if it handled the
command. `dispatch_command` invokes handlers in the fixed order above and raises
`RuntimeError` if no handler accepts a parser-produced command.

Keep presentation helpers in `cli.py` during this task unless a handler is
their only caller; do not combine handler extraction with output redesign.

- [ ] **Step 5: Add command-set compatibility test**

```python
def test_all_existing_commands_remain_available() -> None:
    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )

    assert set(command_action.choices) >= {
        "run",
        "monthly-tickets",
        "template-samples",
        "mysql-init",
        "mysql-import-ticket",
        "mysql-import-month",
        "mysql-import-year",
        "mysql-import-customers",
        "mysql-import-contacts",
        "mysql-import-personnel",
        "mysql-add-partitions",
        "mysql-sync-log",
        "mysql-schema-status",
        "mysql-migrate",
        "import-erp",
        "import-customer-account",
        "generate-revenue-summary",
        "metric-month",
        "metric-ticket",
        "probe",
        "dictionary",
    }
```

- [ ] **Step 6: Run CLI, handover, and full tests**

```powershell
uv run --all-groups pytest tests/test_cli.py tests/test_handover_guide.py tests/test_repository_security.py -q
uv run work_order_process --help
uv run erp-merge --help
uv run --all-groups pytest -q
```

Expected: all tests and both help commands pass.

- [ ] **Step 7: Commit CLI compatibility split**

```powershell
git add src/work_order_process/cli.py src/work_order_process/cli_commands tests/test_cli.py tests/test_handover_guide.py
git diff --cached --check
git commit -m "refactor: split CLI command handlers"
```

---

### Task 8: Ruff Formatting and Static Checks

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.gitignore`
- Modify: `.github/workflows/test.yml`
- Modify: `tests/test_dependency_groups.py`
- Modify: `tests/test_repository_security.py`
- Mechanically format: `src/**/*.py`
- Mechanically format: `tests/**/*.py`

**Interfaces:**
- Adds development tools: `ruff`, `pytest-cov`, `coverage`
- Adds CI checks: Ruff lint, Ruff format, coverage floor

- [ ] **Step 1: Write the development-dependency contract test**

```python
def test_quality_tools_are_development_only() -> None:
    config = _pyproject()
    dev = config["dependency-groups"]["dev"]
    runtime = config["project"]["dependencies"]

    assert any(item.startswith("ruff") for item in dev)
    assert any(item.startswith("pytest-cov") for item in dev)
    assert any(item.startswith("coverage") for item in dev)
    assert not any(item.startswith("ruff") for item in runtime)


def test_coverage_runtime_file_is_ignored() -> None:
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".coverage" in text
```

- [ ] **Step 2: Run the dependency test and verify failure**

```powershell
uv run --all-groups pytest tests/test_dependency_groups.py -q
```

Expected: failure because quality dependencies are absent.

- [ ] **Step 3: Add the development tools and quality configuration**

Run:

```powershell
uv add --dev ruff pytest-cov coverage
```

Add:

```toml
[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "B", "UP"]

[tool.coverage.run]
branch = true
source = ["work_order_process"]

[tool.coverage.report]
fail_under = 70
show_missing = true
skip_covered = true
```

Do not add `omit` entries for application modules.
Add `.coverage` and `htmlcov/` to `.gitignore`.

- [ ] **Step 4: Verify the dependency test and commit the tool configuration**

```powershell
uv run --all-groups pytest tests/test_dependency_groups.py -q
git add pyproject.toml uv.lock .gitignore tests/test_dependency_groups.py
git diff --cached --check
git commit -m "build: add Python quality tools"
```

Expected: the dependency test passes and no source file is formatted in this
commit.

- [ ] **Step 5: Run Ruff once and record the exact failures**

```powershell
uv run --all-groups ruff check src tests
uv run --all-groups ruff format --check src tests
```

Expected: failure on the pre-existing style and import layout.

- [ ] **Step 6: Apply only mechanical formatting and safe lint fixes**

```powershell
uv run --all-groups ruff check --fix src tests
uv run --all-groups ruff format src tests
uv run --all-groups ruff check src tests
uv run --all-groups ruff format --check src tests
uv run --all-groups pytest -q
```

If Ruff reports a behavior-sensitive rule that cannot be auto-fixed, change the
smallest expression preserving behavior and run its owning test file before
continuing. Do not suppress a rule globally to avoid a real defect.

- [ ] **Step 7: Commit the mechanical format separately**

```powershell
git add src tests
git diff --cached --check
git commit -m "style: apply Ruff formatting"
```

- [ ] **Step 8: Write the CI contract test and verify failure**

```python
def test_ci_runs_lint_format_and_coverage_gate() -> None:
    text = (PROJECT_ROOT / ".github/workflows/test.yml").read_text(
        encoding="utf-8"
    )

    assert "ruff check src tests" in text
    assert "ruff format --check src tests" in text
    assert "--cov=work_order_process" in text
    assert "--cov-fail-under=70" in text
```

Run:

```powershell
uv run --all-groups pytest tests/test_repository_security.py -q
```

Expected: failure because CI does not yet run the quality commands.

- [ ] **Step 9: Add CI quality commands and run contract tests**

CI order:

```yaml
- name: Check formatting
  run: uv run --all-groups ruff format --check src tests
- name: Lint
  run: uv run --all-groups ruff check src tests
- name: Run tests with coverage
  run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 -q
```

Run:

```powershell
uv run --all-groups pytest tests/test_dependency_groups.py tests/test_repository_security.py -q
```

Expected: contract tests pass.

- [ ] **Step 10: Commit CI quality gates**

```powershell
git add .github/workflows/test.yml tests/test_dependency_groups.py tests/test_repository_security.py
git diff --cached --check
git commit -m "ci: enforce lint format and coverage"
```

---

### Task 9: Raise Real Coverage from 55% to 70%

**Files:**
- Create: `tests/test_api_characterization.py`
- Create: `tests/test_cli_handlers.py`
- Create: `tests/test_monthly_export_characterization.py`
- Create: `tests/test_personnel_import_characterization.py`
- Create: `tests/test_time_metrics_characterization.py`
- Expand: `tests/test_customer_account_import.py`
- Expand: `tests/test_customer_contact_sync.py`
- Expand: `tests/test_ticket_import.py`
- Expand other owning test files only when the coverage report identifies an uncovered public behavior.

**Interfaces:**
- Consumes only existing public or deliberately extracted internal interfaces.
- Produces no production behavior changes.
- Raises total statement and branch coverage to at least 70% without exclusions.

- [ ] **Step 1: Run the new 70% gate and save the uncovered-module report**

```powershell
uv run --all-groups pytest --cov=work_order_process --cov-report=term-missing --cov-fail-under=70 -q
```

Expected: the suite may fail the 70% threshold; use the exact missing line
ranges to select the next characterization test.

- [ ] **Step 2: Cover API response, pagination, probe, and malformed JSON branches**

`tests/test_api_characterization.py` must exercise:

```text
successful and failed endpoint probes
GET/POST endpoint fallback
empty and populated pagination envelopes
declared totals and max-page stop
ticket/company/contact/support/template detail extraction
invalid JSON escape repair and unrecoverable JSON
record timestamp parsing and since filtering
```

Use `httpx.MockTransport` for request/response behavior. Assert returned domain
values, requested paths, methods, and pagination parameters rather than merely
asserting mock call counts.

- [ ] **Step 3: Cover CLI handler success and validation branches**

`tests/test_cli_handlers.py` must call each handler with a minimal namespace and
monkeypatched domain function, asserting:

```text
the selected command invokes exactly its domain operation
required file/date arguments fail through parser.error
schema status is read-only
schema migration is invoked only by mysql-migrate
database commands run schema preflight
nonmatching handlers return False
```

Keep one test per command family instead of one test containing every command.

- [ ] **Step 4: Cover monthly export file reuse, overwrite, failure, and sampling**

`tests/test_monthly_export_characterization.py` must use `tmp_path` and fake
clients to exercise:

```text
existing output skipped without overwrite
overwrite rewrites deterministic JSON
empty month writes an empty collection
sample detail failure adds failed ID without discarding successful details
template sampling respects seed and sample size
```

- [ ] **Step 5: Cover personnel parsing and import outcomes**

`tests/test_personnel_import_characterization.py` must exercise:

```text
numeric employee numbers normalized without decimal suffix
blank rows ignored
duplicate employee numbers update rather than double count
database row failure rolls back and reports failure
missing required workbook headers fail before database writes
```

- [ ] **Step 6: Cover time-metric query and export branches**

`tests/test_time_metrics_characterization.py` must exercise:

```text
missing start/end, invalid dates, reversed times
month query and single-ticket query
custom-field lookup
successful JSON export to tmp_path
metric-code filtering and unknown metric error
```

- [ ] **Step 7: Cover the new atomic, failure, migration, and split-module branches**

Expand the Task 1-7 test files until every newly introduced module reaches at
least 80% statement coverage. Required error branches include:

```text
failure summary limit zero
stage insert count mismatch
formal publish mismatch
batch write fallback
API retry exhaustion
migration checksum drift
legacy facade delegation
unknown CLI dispatch command
```

- [ ] **Step 8: Repeat coverage after each focused test file**

Run:

```powershell
uv run --all-groups pytest tests/test_api_characterization.py -q
uv run --all-groups pytest tests/test_cli_handlers.py -q
uv run --all-groups pytest tests/test_monthly_export_characterization.py -q
uv run --all-groups pytest tests/test_personnel_import_characterization.py -q
uv run --all-groups pytest tests/test_time_metrics_characterization.py -q
uv run --all-groups pytest --cov=work_order_process --cov-report=term-missing --cov-fail-under=70 -q
```

Expected: focused files pass and total coverage is at least 70%.

- [ ] **Step 9: Commit coverage tests**

```powershell
git add tests
git diff --cached --check
git commit -m "test: raise application coverage to 70 percent"
```

---

### Task 10: Documentation, Compatibility Index, and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/project_handover_guide.md`
- Modify: `docs/production_operations.md`
- Modify: `docs/database_usage.md`
- Modify: `tests/test_handover_guide.py`

**Interfaces:**
- Documents new modules, failure reports, retry behavior, schema commands, CI commands, and production migration procedure.
- Preserves the handbook as the single long-lived entry point.

- [ ] **Step 1: Run the dynamic handbook tests and capture missing symbols/commands**

```powershell
uv run --all-groups pytest tests/test_handover_guide.py -q
```

Expected: failure listing new modules, top-level symbols, and CLI commands that
the handbook does not yet index.

- [ ] **Step 2: Update the handbook and focused documents**

Add exact command procedures:

```powershell
uv run work_order_process mysql-schema-status
uv run work_order_process mysql-migrate
uv run work_order_process mysql-import-personnel --personnel-file "人员信息名单.xls"
```

Document deployment order:

```text
1. Deploy one verified commit.
2. Stop the daily runner.
3. Run mysql-schema-status.
4. Review pending versions and backup state.
5. Run mysql-migrate explicitly.
6. Run mysql-schema-status again and require current state.
7. Start the daily runner.
8. Check sync_task_log and service logs.
```

Document customer-account atomic replacement, structured failure fields,
bounded safe summaries, retryable statuses, module compatibility facades, and
the complete local quality command set.

- [ ] **Step 3: Run documentation, security, and command checks**

```powershell
uv run --all-groups pytest tests/test_handover_guide.py tests/test_repository_security.py -q
uv run work_order_process --help
uv run erp-merge --help
```

Expected: all checks pass.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md docs/project_handover_guide.md docs/production_operations.md docs/database_usage.md tests/test_handover_guide.py
git diff --cached --check
git commit -m "docs: document compatibility hardening workflow"
```

- [ ] **Step 5: Run the complete final verification gate**

```powershell
uv sync --all-groups --locked
uv lock --check
uv run --all-groups ruff check src tests
uv run --all-groups ruff format --check src tests
uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 -q
uv run --all-groups python -m compileall -q src tests
uv run work_order_process --help
uv run erp-merge --help
git diff --check
git status --short
```

Expected:

```text
all tests pass
coverage >= 70%
Ruff lint and format clean
lock and compile checks succeed
both CLI help commands succeed
git diff --check emits no errors
git status contains only the user's original untracked files plus no task changes
```

- [ ] **Step 6: Review the commit series**

```powershell
git log --oneline --decorate -12
git status --short
```

Confirm each task is independently represented and no user-owned untracked file
was staged.
