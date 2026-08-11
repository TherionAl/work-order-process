# 湖北质检自动调度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在生产服务器按固定自然日自动生成湖北质检报告，并让月结复检覆盖周检结果。

**Architecture:** 使用纯函数计算周检和月结的左闭右开日期窗口；独立 Python 入口依次调用现有报告和回写 CLI。两套 oneshot systemd service/timer 隔离周检与月结，月结通过显式参数开启覆盖。

**Tech Stack:** Python 3.12、subprocess、openpyxl、pytest、systemd 219。

## Global Constraints

- 仅允许湖北省专用报告进入回写流程。
- 周检日期为每月 8、15、22、29 日，月结日期为每月 1 日，执行时间为 05:17 Asia/Shanghai。
- 周检不覆盖已有结果；月结覆盖 `field_1447` 和不通过原因，并清理达标工单的旧原因。
- 所有 API 写入继续逐条回读验证并写 JSONL 审计。
- 保留现有报告和回写 CLI 的默认行为。

---

### Task 1: 月结覆盖计划

**Files:**
- Modify: `src/work_order_process/ticket_writeback.py`
- Modify: `scripts/apply_hubei_sampling_status.py`
- Test: `tests/test_ticket_writeback.py`

**Interfaces:**
- Consumes: `overwrite_existing: bool`。
- Produces: `build_sampling_status_plan(..., overwrite_existing=False)` 和 `build_failure_reason_plan(..., overwrite_existing=False)`。

- [ ] **Step 1: 写失败测试**

分别断言月结会覆盖不同状态、跳过相同状态、覆盖不同原因，并为达标工单生成清空旧原因的计划。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_ticket_writeback.py -q`

Expected: FAIL，因为计划函数尚不支持 `overwrite_existing`。

- [ ] **Step 3: 最小实现**

给两个计划函数增加关键字参数；回写 CLI 增加 `--overwrite-existing`，并把该模式写入 run_started 审计事件。

- [ ] **Step 4: 运行测试并确认通过**

Run: `uv run pytest tests/test_ticket_writeback.py -q`

Expected: PASS。

### Task 2: 可确定的报告输出路径和调度入口

**Files:**
- Modify: `scripts/compliance_check.py`
- Create: `src/work_order_process/hubei_quality_schedule.py`
- Create: `scripts/run_scheduled_hubei_quality.py`
- Create: `tests/test_hubei_quality_schedule.py`

**Interfaces:**
- Produces: `QualityWindow`, `scheduled_window(period, run_at)` 和 `run_scheduled_quality(period, run_at=None, apply=True)`。
- 调用报告 CLI 时传递 `--start`、`--end`、`--province 湖北省` 和 `--output-file`；调用回写 CLI 时传递 `--input`、`--apply`，月结额外传递 `--overwrite-existing`。

- [ ] **Step 1: 写日期窗口失败测试**

用字面量断言 2026-08-08 周检为 `[2026-08-01, 2026-08-08)`，2027-01-01 月结为 `[2026-12-01, 2027-01-01)`，非计划日期抛出异常。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_hubei_quality_schedule.py -q`

Expected: FAIL，因为调度模块尚不存在。

- [ ] **Step 3: 实现窗口和命令编排**

生成包含范围和时间戳的唯一 Excel 路径，使用当前 Python 解释器执行两个脚本，并让任何非零退出码直接传播。

- [ ] **Step 4: 增加明确输出文件参数**

报告脚本增加 `--output-file`，与 `--output-dir` 互斥；默认行为保持不变。

- [ ] **Step 5: 运行专项测试**

Run: `uv run pytest tests/test_hubei_quality_schedule.py tests/test_ticket_writeback.py -q`

Expected: PASS。

### Task 3: systemd 部署资产与文档

**Files:**
- Create: `deploy/work-order-hubei-quality-weekly.service`
- Create: `deploy/work-order-hubei-quality-weekly.timer`
- Create: `deploy/work-order-hubei-quality-monthly.service`
- Create: `deploy/work-order-hubei-quality-monthly.timer`
- Modify: `tests/test_deploy_assets.py`
- Modify: `README.md`

**Interfaces:**
- 周检 service 调用 `--period weekly --apply`。
- 月结 service 调用 `--period monthly --apply`。

- [ ] **Step 1: 写部署行为失败测试**

读取 timer/service 并断言 oneshot、准确 OnCalendar、`Persistent=true` 和对应 period 参数。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_deploy_assets.py -q`

Expected: FAIL，因为 unit 文件尚不存在。

- [ ] **Step 3: 增加 unit/timer 和运行说明**

模板沿用 `/opt/work_order_process` 和项目虚拟环境；README 说明周检、月结与手工 dry-run 命令。

- [ ] **Step 4: 运行验证**

Run: `uv run pytest tests/test_deploy_assets.py tests/test_hubei_quality_schedule.py tests/test_ticket_writeback.py -q`

Expected: PASS。

### Task 4: 全量验证和生产部署

**Files:**
- Modify: production release and four units on `172.18.169.231`。

**Interfaces:**
- Consumes: 已通过验证的 Git 提交。
- Produces: 新 release、两个 active/enabled timer 和不变的原常驻服务。

- [ ] **Step 1: 本地完整验证**

Run: `uv run --all-groups pytest -q`

Run: `uv run ruff check src/work_order_process/ticket_writeback.py src/work_order_process/hubei_quality_schedule.py scripts/apply_hubei_sampling_status.py scripts/compliance_check.py scripts/run_scheduled_hubei_quality.py tests/test_ticket_writeback.py tests/test_hubei_quality_schedule.py tests/test_deploy_assets.py`

Run: `git diff --check`

- [ ] **Step 2: 提交并推送 main**

仅暂存本计划列出的文件，保留工作区中用户的无关修改。

- [ ] **Step 3: 发布新 release 并安装 timer**

复制当前生产 `.env`，同步依赖；安装四个 unit 时把模板路径替换为实际 release 目录，执行 `daemon-reload` 并启用 timer。

- [ ] **Step 4: 服务器只读核验**

运行 schema 预检、报告 dry-run、`systemd-analyze verify`、`systemctl is-active/is-enabled`、`systemctl list-timers` 和 journal 错误检查；不手工执行 `--apply`。
