# 湖北工单专项分析优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让湖北工单明细导出和合规检查可正确区分工单版本、可复现地按参数执行，并具备自动化回归保护。

**Architecture:** 在 `work_order_process.hubei_analysis` 集中放置筛选参数、数据库连接参数、工单版本字段索引和重复判定等纯逻辑。两个可执行脚本仅负责参数解析、SQL 查询和 Excel 输出；自定义字段始终以 `(ticket_id, create_dt)` 关联。探索脚本保持只读，不纳入本次正式报表入口。

**Tech Stack:** Python 3.12, PyMySQL, openpyxl, pytest, ruff。

## Global Constraints

- 仅读取生产 MySQL；不得在运行时执行 DDL/DML。
- 默认筛选保持湖北省、近 30 天、已解决/已关闭、服务目录包含生产环境异常处理且未申请总部协作。
- 时间范围使用 `[start, end)`，命令行参数可覆盖默认值。
- 同一工单版本的唯一关联键是 `(ticket_id, create_dt)`。
- 重复判定仅在同日、同客户、规范化标题相同且问题描述相似度达到阈值时执行；客户名称为空的记录不判重。
- 不修改本次范围外的既有失败测试。

---

### Task 1: 提取可测试的湖北分析公共逻辑

**Files:**
- Create: `src/work_order_process/hubei_analysis.py`
- Create: `tests/test_hubei_analysis.py`

**Interfaces:**
- Produces `AnalysisScope`, `index_custom_fields_by_ticket_version`, `mark_duplicate_results`, `mysql_config_from_environment`.
- Consumes包含 `ticket_id`、`create_dt`、`field_name`、`field_key`、`field_value` 的数据库行字典。

- [ ] **Step 1: 写失败测试**

```python
def test_index_custom_fields_keeps_ticket_versions_separate() -> None:
    fields = index_custom_fields_by_ticket_version([...])
    assert fields[(101, newer_time)]["问题描述"] == "新版本"
    assert fields[(101, older_time)]["问题描述"] == "旧版本"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_hubei_analysis.py -q`

Expected: FAIL，因为模块和接口尚不存在。

- [ ] **Step 3: 实现最小公共逻辑**

```python
def index_custom_fields_by_ticket_version(rows):
    indexed = {}
    for row in rows:
        indexed.setdefault((row["ticket_id"], row["create_dt"]), {})
    return indexed
```

同时实现标题规范化、空客户跳过和“仅后一条标记为重复”的重复判断。

- [ ] **Step 4: 运行目标测试并确认通过**

Run: `uv run pytest tests/test_hubei_analysis.py -q`

Expected: PASS。

### Task 2: 修正导出脚本的版本关联和可复现参数

**Files:**
- Modify: `scripts/export_hubei_prod_exception.py`
- Test: `tests/test_hubei_analysis.py`

**Interfaces:**
- Consumes `AnalysisScope` 和按 `(ticket_id, create_dt)` 建立的自定义字段映射。
- Produces带实际筛选时间范围的 Excel 明细文件。

- [ ] **Step 1: 写失败测试**

```python
def test_selected_ticket_version_does_not_receive_fields_from_other_version() -> None:
    assert field_values_for_version(fields, (101, newer_time))["问题描述"] == "新版本"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_hubei_analysis.py -q`

Expected: FAIL，因为版本限定的字段读取尚未实现。

- [ ] **Step 3: 最小实现**

将自定义字段 SQL 改为 `(ticket_id, create_dt)` 成对条件，使用公共字段索引；增加 `--start`、`--end`、`--province`、`--service-catalog-contains`、`--output-dir` 和 `--limit` 参数。

- [ ] **Step 4: 运行目标测试并确认通过**

Run: `uv run pytest tests/test_hubei_analysis.py -q`

Expected: PASS。

### Task 3: 修正合规判重和报表可追溯性

**Files:**
- Modify: `scripts/compliance_check.py`
- Test: `tests/test_hubei_analysis.py`

**Interfaces:**
- Consumes `mark_duplicate_results(results, threshold=0.8)`。
- Produces筛选参数、实际时间范围和规则编号一致的合规 Excel 报表。

- [ ] **Step 1: 写失败测试**

```python
def test_duplicate_check_requires_matching_title() -> None:
    results = mark_duplicate_results([same_customer_different_title_rows])
    assert not any(item["is_duplicate"] for item in results)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_hubei_analysis.py -q`

Expected: FAIL，因为当前实现忽略标题并会误判。

- [ ] **Step 3: 最小实现**

将重复分组键扩展为日期、非空客户名、规范化标题；复用公共参数解析和数据库配置；在 Excel 中记录实际筛选参数，并把终端摘要中的“规则4-非重复”更正为“规则5-非重复”。

- [ ] **Step 4: 运行目标测试并确认通过**

Run: `uv run pytest tests/test_hubei_analysis.py -q`

Expected: PASS。

### Task 4: 静态检查和交付说明

**Files:**
- Modify: `README.md`
- Modify: `scripts/explore_hubei_data.py`
- Modify: `scripts/explore_hubei_data2.py`
- Modify: `scripts/check_service_catalog.py`

- [ ] **Step 1: 整理脚本风格并为探索脚本添加统一的时间/省份参数**

```python
parser.add_argument("--days", type=int, default=30)
parser.add_argument("--province", default="湖北省")
```

- [ ] **Step 2: 增加 README 使用示例和数据边界说明**

写明脚本仅执行 SELECT、会在 `output/` 生成含工单内容的 Excel、输出目录已被 Git 忽略。

- [ ] **Step 3: 运行验证**

Run: `uv run pytest tests/test_hubei_analysis.py -q; uv run ruff check src/work_order_process/hubei_analysis.py tests/test_hubei_analysis.py scripts/export_hubei_prod_exception.py scripts/compliance_check.py scripts/explore_hubei_data.py scripts/explore_hubei_data2.py scripts/check_service_catalog.py`

Expected: 所有新增目标测试和范围内 ruff 检查通过。
