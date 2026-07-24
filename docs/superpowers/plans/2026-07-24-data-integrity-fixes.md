# 数据完整性修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 ERP 快照、营收汇总和工单定时同步在失败、重跑和跨年场景下仍保持数据准确。

**Architecture:** ERP 先写连接级临时表，校验后以单事务完整替换正式快照；营收按年月完整替换并在计算前验证快照；共享业务规则从单一配置读取。调度月份计算提取为纯函数，任务失败最终向 APScheduler 传播。

**Tech Stack:** Python 3.14、pandas、PyMySQL、MySQL 8、APScheduler、pytest、openpyxl、uv

## Global Constraints

- 不扫描或修改 `data/`。
- 所有生产代码变更必须先有能正确失败的测试。
- ERP 正式表只能在整批数据通过校验后更新。
- 不在日志、异常或测试中输出真实凭据。
- 服务器和真实数据库写操作不属于本计划。

---

### Task 1: 统一体系工程师映射并严格校验金额

**Files:**
- Modify: `config/erp_merge_rules.toml`
- Modify: `src/work_order_process/erp_import.py`
- Modify: `src/work_order_process/erp_merge/mapping.py`
- Test: `tests/erp_merge/test_mapping.py`
- Test: `tests/erp_merge/test_pipeline.py`
- Test: `tests/test_snapshot_imports.py`

**Interfaces:**
- Produces: `parse_number_series(series: pd.Series, *, field_name: str = "") -> pd.Series`
- Produces: `InvalidNumericValue(ValueError)`
- Consumes: `load_config()["体系工程师"]`

- [ ] **Step 1: 写映射一致性和非法金额失败测试**

```python
def test_jilin_engineer_uses_single_confirmed_mapping(config):
    frame = pd.DataFrame({"营销平台": ["吉林分公司"]})
    assert add_engineer_column(frame, config).loc[0, "体系工程师"] == "梁通"

def test_parse_number_series_rejects_nonempty_invalid_value():
    with pytest.raises(InvalidNumericValue, match="产品金额.*第 3 行.*bad"):
        parse_number_series(pd.Series(["1", "", "bad"]), field_name="产品金额")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/erp_merge/test_mapping.py tests/erp_merge/test_pipeline.py tests/test_snapshot_imports.py -q`

Expected: 吉林映射仍得到“苏远星”，非法金额仍被转换为 0。

- [ ] **Step 3: 实现单一映射和严格解析**

```python
class InvalidNumericValue(ValueError):
    pass

def parse_number_series(series: pd.Series, *, field_name: str = "") -> pd.Series:
    text = series.fillna("").astype(str).str.strip()
    cleaned = text.str.replace(",", "", regex=False).str.replace("，", "", regex=False)
    cleaned = cleaned.str.rstrip("%")
    parsed = pd.to_numeric(cleaned.replace({"/": "", "": pd.NA}), errors="coerce")
    invalid = text.ne("") & text.ne("/") & parsed.isna()
    if invalid.any():
        index = invalid[invalid].index[0]
        raise InvalidNumericValue(
            f"{field_name or '数值字段'}第 {int(index) + 2} 行无法解析: {text.loc[index]!r}"
        )
    result = parsed.fillna(0.0).astype("float64")
    result.loc[text.str.endswith("%")] /= 100
    return result
```

将 TOML 中吉林映射改为“梁通”，删除 `SYSTEM_ENGINEER_BY_SALES_PLATFORM`，让入库阶段接收或加载同一配置映射。

- [ ] **Step 4: 运行相关测试**

Run: `uv run --no-default-groups pytest tests/erp_merge/test_mapping.py tests/erp_merge/test_pipeline.py tests/test_snapshot_imports.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add config/erp_merge_rules.toml src/work_order_process/erp_import.py src/work_order_process/erp_merge/mapping.py tests/erp_merge/test_mapping.py tests/erp_merge/test_pipeline.py tests/test_snapshot_imports.py
git commit -m "fix: unify ERP mappings and validate numeric inputs"
```

### Task 2: ERP 临时表导入和原子快照发布

**Files:**
- Modify: `src/work_order_process/erp_import.py`
- Test: `tests/test_erp_import.py`

**Interfaces:**
- Produces: `ERPImportError(RuntimeError)`
- Produces: `_validate_erp_row(row: Mapping[str, object], source_row: int) -> None`
- Produces: `_publish_staged_snapshot(cursor: Any, create_date: str, expected_rows: int) -> None`
- Produces: `import_erp_dataframe(...) -> dict`，成功报告包含 `published_rows`

- [ ] **Step 1: 写失败不发布、同日完整替换和业务键校验测试**

```python
def test_import_error_rolls_back_without_publishing(monkeypatch):
    connection = FailingStageConnection(fail_on_contract="BAD")
    with pytest.raises(erp_import.ERPImportError, match="BAD"):
        erp_import.import_erp_dataframe(_config(), _frame(["OK", "BAD"]))
    assert connection.formal_table_changes == []

def test_publish_replaces_same_day_snapshot():
    cursor = RecordingCursor()
    erp_import._publish_staged_snapshot(cursor, "20260724", 2)
    assert "DELETE FROM erp_data WHERE create_date = %s" in cursor.statements[-3][0]
    assert "INSERT INTO erp_data" in cursor.statements[-2][0]

def test_import_rejects_incomplete_snapshot_key():
    with pytest.raises(erp_import.ERPImportError, match="item_code"):
        erp_import._validate_erp_row(
            {"contract_id": "C1", "item_code": None, "exec_detail_id": "E1", "create_date": "20260724"},
            2,
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/test_erp_import.py -q`

Expected: 新异常和发布函数不存在。

- [ ] **Step 3: 实现临时表、校验和事务发布**

核心 SQL 顺序固定为：

```sql
CREATE TEMPORARY TABLE erp_data_import_stage ENGINE=InnoDB
AS SELECT <78个导入字段> FROM erp_data WHERE 1 = 0;
INSERT INTO erp_data_import_stage (...) VALUES (...);
START TRANSACTION;
DELETE FROM erp_data WHERE create_date = %s;
INSERT INTO erp_data (...) SELECT ... FROM erp_data_import_stage WHERE create_date = %s;
SELECT COUNT(*) FROM erp_data WHERE create_date = %s;
COMMIT;
```

实现要求：

```python
try:
    _load_stage_rows(cursor, rows, batch_size)
    _validate_staged_snapshot(cursor, create_date, source_rows)
    connection.begin()
    _publish_staged_snapshot(cursor, create_date, source_rows)
    connection.commit()
except Exception as exc:
    connection.rollback()
    raise ERPImportError(message) from exc
```

不得继续使用逐行 `except Exception: skipped += 1`。临时表可分批提交，但正式表删除与插入必须在同一事务。

- [ ] **Step 4: 运行 ERP 导入测试**

Run: `uv run --no-default-groups pytest tests/test_erp_import.py tests/test_snapshot_imports.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add src/work_order_process/erp_import.py tests/test_erp_import.py tests/test_snapshot_imports.py
git commit -m "fix: publish ERP snapshots atomically"
```

### Task 3: ERP 流程年度自动化和发布后导出保护

**Files:**
- Modify: `config/erp_merge_rules.toml`
- Modify: `src/work_order_process/erp_merge/cli.py`
- Test: `tests/erp_merge/test_cli.py`

**Interfaces:**
- Produces: `default_statistics_periods(year: int) -> tuple[tuple[str, str], tuple[str, str]]`
- CLI adds: `--statistics-year`

- [ ] **Step 1: 写 2027 年默认区间及导入失败不导出测试**

```python
def test_default_statistics_periods_follow_selected_year():
    assert default_statistics_periods(2027) == (
        ("2026-01-01", "2026-12-31"),
        ("2027-01-01", "2027-12-31"),
    )

def test_failed_import_does_not_export_document(monkeypatch):
    monkeypatch.setattr(cli, "import_erp_dataframe", Mock(side_effect=ERPImportError("failed")))
    export = Mock()
    monkeypatch.setattr(cli, "export_erp_snapshot_document", export)
    with pytest.raises(ERPImportError):
        cli.main(_valid_args())
    export.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/erp_merge/test_cli.py -q`

Expected: 默认区间函数和 `--statistics-year` 不存在。

- [ ] **Step 3: 实现年度参数**

```python
def default_statistics_periods(year: int):
    return (
        (f"{year - 1}-01-01", f"{year - 1}-12-31"),
        (f"{year}-01-01", f"{year}-12-31"),
    )
```

`--statistics-year` 默认 `datetime.now().year`；四个显式日期参数分别覆盖对应默认值。删除 TOML 固定日期作为运行默认值，但保留其他业务规则。

- [ ] **Step 4: 运行 CLI 和计算测试**

Run: `uv run --no-default-groups pytest tests/erp_merge/test_cli.py tests/erp_merge/test_calculator.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add config/erp_merge_rules.toml src/work_order_process/erp_merge/cli.py tests/erp_merge/test_cli.py
git commit -m "fix: derive ERP allocation periods from statistics year"
```

### Task 4: 营收快照验证和同年月完整替换

**Files:**
- Modify: `src/work_order_process/revenue_summary.py`
- Test: `tests/test_revenue_summary.py`

**Interfaces:**
- Produces: `RevenueSnapshotError(ValueError)`
- Produces: `validate_revenue_snapshot(cursor: Any, create_date: str) -> int`
- Replaces: `save_revenue_rows(cursor, rows)` with `replace_revenue_rows(cursor, year, month, rows) -> None`

- [ ] **Step 1: 写空快照、NULL 分摊和旧平台删除测试**

```python
def test_validate_revenue_snapshot_rejects_missing_snapshot():
    cursor = ScriptedCursor(fetchone_values=[(0, 0, 0)])
    with pytest.raises(RevenueSnapshotError, match="不存在"):
        validate_revenue_snapshot(cursor, "20990101")

def test_validate_revenue_snapshot_rejects_null_allocations():
    cursor = ScriptedCursor(fetchone_values=[(10, 1, 0)])
    with pytest.raises(RevenueSnapshotError, match="分摊字段"):
        validate_revenue_snapshot(cursor, "20260724")

def test_replace_revenue_rows_deletes_month_before_insert():
    cursor = _FakeCursor()
    replace_revenue_rows(cursor, 2026, 6, [_row("厦门分公司")])
    assert cursor.executed[0] == (
        "DELETE FROM ops_service_revenue_monthly WHERE stat_year = %s AND stat_month = %s",
        (2026, 6),
    )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/test_revenue_summary.py -q`

Expected: 验证和完整替换接口不存在。

- [ ] **Step 3: 实现快照保护和完整替换**

验证 SQL：

```sql
SELECT
  COUNT(*),
  SUM(prev_year_adjusted_amort IS NULL),
  SUM(cur_year_adjusted_amort IS NULL)
FROM erp_data
WHERE create_date = %s;
```

生成指标后若 `metrics` 为空则抛出 `RevenueSnapshotError`。持久化时先按年月删除，再插入全部平台，校验插入数等于目标数，发生异常由连接上下文回滚。

- [ ] **Step 4: 运行营收测试**

Run: `uv run --no-default-groups pytest tests/test_revenue_summary.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add src/work_order_process/revenue_summary.py tests/test_revenue_summary.py
git commit -m "fix: validate and replace monthly revenue summaries"
```

### Task 5: 修复跨年同步和失败传播

**Files:**
- Modify: `src/work_order_process/daily_runner.py`
- Create: `tests/test_daily_runner.py`

**Interfaces:**
- Produces: `maintenance_months(now: datetime, rolling_months: int = 4) -> list[tuple[int, int]]`
- Produces: `ScheduledSyncError(RuntimeError)`
- Changes: `sync_tickets_for_month(...) -> dict[str, object]`

- [ ] **Step 1: 写跨年月份和失败传播测试**

```python
def test_maintenance_months_cover_old_previous_year_months_in_january():
    assert maintenance_months(datetime(2027, 1, 1)) == [
        (2026, month) for month in range(1, 10)
    ]

def test_daily_job_raises_after_processing_failed_month(monkeypatch):
    reports = iter([
        {"failed": 0},
        {"failed": 2, "failed_ids": ["1", "2"]},
        {"failed": 0},
        {"failed": 0},
    ])
    monkeypatch.setattr(daily_runner, "sync_tickets_for_month", lambda *_: next(reports))
    with pytest.raises(ScheduledSyncError, match="2"):
        daily_runner.job_sync_tickets_daily()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run --no-default-groups pytest tests/test_daily_runner.py -q`

Expected: 月份函数和异常类型不存在。

- [ ] **Step 3: 实现纯月份计算和任务汇总**

```python
def maintenance_months(now: datetime, rolling_months: int = 4):
    rolling = {
        ((now - relativedelta(months=offset)).year,
         (now - relativedelta(months=offset)).month)
        for offset in range(rolling_months)
    }
    target_year = now.year if now.month >= rolling_months else now.year - 1
    return [
        (target_year, month)
        for month in range(1, 13)
        if (target_year, month) not in rolling
        and datetime(target_year, month, 1) < now
    ]
```

`sync_tickets_for_month` 不吞异常并返回报告；日常任务处理完计划月份后，只要报告中 `failed > 0` 或捕获到异常，就抛出汇总异常。

- [ ] **Step 4: 运行调度和完整相关测试**

Run: `uv run --no-default-groups pytest tests/test_daily_runner.py tests/test_mysql_storage.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```powershell
git add src/work_order_process/daily_runner.py tests/test_daily_runner.py
git commit -m "fix: make scheduled sync failures visible"
```

### Task 6: 数据正确性阶段总验证

**Files:**
- Test: all project tests

- [ ] **Step 1: 运行完整测试**

Run: `uv run --no-default-groups pytest -q`

Expected: 全部通过，无 warning 或 error。

- [ ] **Step 2: 检查锁文件和差异**

Run: `uv lock --check`

Expected: `Resolved` 或锁文件无需更新。

Run: `git diff --check`

Expected: 无输出。

- [ ] **Step 3: 检查未跟踪用户文件未被纳入**

Run: `git status --short`

Expected: `data/` 下三个用户文件仍为未跟踪，所有任务文件均已提交。
