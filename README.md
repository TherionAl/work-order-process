# work_order_process：工单数据获取、解析与 MySQL 入库

本项目从“帮我吧”工单系统 API 获取工单数据，支持月度 JSON 导出、样本详情解析、数据字典中文化，以及面向 MySQL 数据湖的批量入库。

当前版本是组合版：以 Claude_code 版的 MySQL 数据湖能力为主线，同时保留根目录版本中更实用的轻量月度导出命令 `monthly-tickets` 和样本详情并发参数 `--detail-workers`。

## 核心能力

1. 按创建时间导出指定年份或月份的工单列表。
2. 生成三段式详情 JSON：`raw`、`value_resolved`、`chinese`。
3. 保留原始 ID，同时补充可读名称字段，便于入库后分析。
4. 支持工单、客户、联系人、ERP、客户台账及营收汇总数据统一进入 MySQL 数据湖。
5. 支持按月分区、未来分区创建、按月/按年批量导入、断点续跑和同步日志查看。
6. 支持新旧 ERP 原始文件直接合并、计算年度分摊、原子替换数据库快照并导出数据库同版 Excel。

## 环境准备

```powershell
uv sync
```

接口和数据库凭据只从环境变量或本机 `.env` 读取，不得写入 `agents.md` 或提交到 Git。

`.env` 示例：

```dotenv
WORKORDER_USERNAME=your_username
WORKORDER_PASSWORD=your_password
WORKORDER_BASE_URL=https://workorder.bosssoft.com.cn/api/v1

WORKORDER_MYSQL_HOST=127.0.0.1
WORKORDER_MYSQL_PORT=3306
WORKORDER_MYSQL_USER=workorder
WORKORDER_MYSQL_PASSWORD=your_mysql_password
WORKORDER_MYSQL_DATABASE=work_order_datalake
```

## 日常导出

导出 2025 年月度工单合集和每月样本详情：

```powershell
uv run work_order_process run
```

只跑指定月份：

```powershell
uv run work_order_process run --year 2026 --month 6
```

调试时限制每月最多拉取 10 条列表记录：

```powershell
uv run work_order_process run --year 2026 --limit-per-month 10 --overwrite
```

调整样本详情并发线程数：

```powershell
uv run work_order_process run --year 2026 --detail-workers 4
```

只导出月度工单合集，不拉取详情样本：

```powershell
uv run work_order_process monthly-tickets --year 2026
uv run work_order_process monthly-tickets --year 2026 --month 6
```

按工单模板抽样：

```powershell
uv run work_order_process template-samples --year 2026 --month 6 --sample-size 3 --seed 202606 --overwrite
```

探测认证和接口：

```powershell
uv run work_order_process probe
```

导出数据字典：

```powershell
uv run work_order_process dictionary
```

## MySQL 入库

初始化 5 表结构和分区：

```powershell
uv run work_order_process mysql-init
```

已有数据库的结构检查和升级必须分开执行：`mysql-schema-status` 只读报告版本、待执行
迁移和 checksum 漂移；`mysql-migrate` 才会显式执行 DDL。生产迁移前先完成可恢复备份并
停止 daily runner，具体 8 步顺序见[项目接手手册](docs/project_handover_guide.md)和
[生产运行说明](docs/production_operations.md)。

```powershell
uv run work_order_process mysql-schema-status
uv run work_order_process mysql-migrate
```

导入单条工单：

```powershell
uv run work_order_process mysql-import-ticket --ticket-id 22256891
```

导入某个月：

```powershell
uv run work_order_process mysql-import-month --year 2025 --month 1
```

导入全年：

```powershell
uv run work_order_process mysql-import-year --year 2025
```

低速试跑建议：

```powershell
uv run work_order_process mysql-import-month --year 2025 --month 1 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

导入客户和联系人：

```powershell
uv run work_order_process mysql-import-customers
uv run work_order_process mysql-import-contacts
```

导入本地人员名单时必须显式传入 `.xls` 文件；该参数没有默认文件：

```powershell
uv run work_order_process mysql-import-personnel --personnel-file "人员信息名单.xls"
```

提前创建未来分区：

```powershell
uv run work_order_process mysql-add-partitions --months-ahead 6
```

查看最近同步日志：

```powershell
uv run work_order_process mysql-sync-log --log-limit 20
```

## 湖北生产环境异常工单分析

以下专项脚本只执行 MySQL `SELECT`，不会在运行时创建、修改或删除生产表；Excel 报表写入
本地 `output/`（该目录已被 Git 忽略）。报表包含工单正文、联系人和客户信息，应按内部数据
规范保存和传递。

导出工单明细。默认范围是运行时刻往前 30 天；时间使用 `[start, end)`，即开始时间包含、结束
时间不包含。自定义字段始终按工单 ID 与创建时间成对关联，避免混入同一工单的其他版本。

```powershell
uv run python scripts/export_hubei_prod_exception.py `
  --start 2026-08-01T00:00:00 `
  --end 2026-09-01T00:00:00 `
  --output-dir output/hubei_prod_exception
```

执行内容合规检查。重复工单只会在同日、同客户、同标题且问题描述相似度达到 80% 时标记；
客户或标题为空的记录不参与重复判定。

```powershell
uv run python scripts/compliance_check.py `
  --start 2026-08-01T00:00:00 `
  --end 2026-09-01T00:00:00 `
  --limit 500
```

两份报表都可使用 `--province`、`--service-catalog-contains`、`--output-dir` 和 `--limit` 覆盖
默认值；先运行 `--help` 查看完整参数。`scripts/explore_hubei_data*.py` 与
`scripts/check_service_catalog.py` 是只读字段探查工具，不作为正式报表入口。

将合规检查结论回写到工单时，默认只做预演；明确增加 `--apply` 才会写入。回写脚本强制
校验报告中的机器可读范围，只接受报告类型为湖北专项且省份为“湖北省”的新报告；旧报告、
缺少范围页或其他省份的报告都会在调用接口前被拒绝。已有非空结果不会覆盖，写入后会逐条
回读验证并在 `output/compliance_writeback/` 保存 JSONL 审计记录。

```powershell
uv run python scripts/apply_hubei_sampling_status.py `
  --input output/compliance_check/故障单合规性检查_YYYYMMDD_HHMMSS.xlsx

uv run python scripts/apply_hubei_sampling_status.py `
  --input output/compliance_check/故障单合规性检查_YYYYMMDD_HHMMSS.xlsx `
  --apply
```

生产服务器使用独立 systemd timer 自动执行湖北质检，时间均为北京时间 05:17：

- 每月 8、15、22、29 日检查前一个完整 7 天分段，仅填写尚未设置的抽检字段。
- 每月 1 日检查上一个完整自然月，覆盖周检结论和不通过原因；最终达标时清空旧原因。
- timer 设置 `Persistent=true`，服务器错过计划时刻后恢复会按最近一次计划窗口补跑。

手工预演时必须指定与周期匹配的执行日；不加 `--apply` 不会写入接口：

```powershell
uv run python scripts/run_scheduled_hubei_quality.py `
  --period weekly `
  --run-at 2026-08-15T05:17:00

uv run python scripts/run_scheduled_hubei_quality.py `
  --period monthly `
  --run-at 2026-08-01T05:17:00 `
  --apply
```

月结覆盖只能由 `--period monthly --apply` 的调度入口自动开启；直接运行回写脚本时，只有
额外明确传入 `--overwrite-existing` 才会覆盖现有结果。每次执行的 Excel 和 JSONL 审计仍分别
保存在 `output/compliance_check/` 和 `output/compliance_writeback/`。

## ERP、客户台账和营收

将新旧 ERP 原始文件直接合并、计算 2026 年度分摊、原子写入数据库，再从数据库导出
单 Sheet 文档版：

```powershell
uv run erp-merge `
  --config "新旧ERP字段对照.xlsx" `
  --input-new "新ERP.xlsx" `
  --input-old "旧ERP.xlsx" `
  --statistics-year 2026 `
  --document-output "output/erp_merge/新旧ERP数据库快照文档版.xlsx"
```

导入已经整理好的标准 ERP 工作簿：

```powershell
uv run work_order_process import-erp --erp-file "标准ERP.xlsx"
```

导入客户台账快照：

```powershell
uv run work_order_process import-customer-account `
  --customer-account-file "客户台账.xlsx" `
  --create-date 20260724
```

生成并写入月度营收汇总：

```powershell
uv run work_order_process generate-revenue-summary `
  --year 2026 `
  --month 6 `
  --erp-create-date 20260717 `
  --revenue-target-file "运维服务营收数据表.xlsx"
```

人工核对时增加 `--revenue-preview`，只输出 Excel，不修改营收汇总表。

## 输出目录

月度工单合集：

```text
output/2026_monthly_tickets/
  2026-01_tickets.json
  2026-02_tickets.json
```

样本详情：

```text
output/2026_monthly_sample_details/
  2026-01_sample_details_raw.json
  2026-01_sample_details_value_resolved.json
  2026-01_sample_details_chinese.json
```

MySQL 导入失败日志：

```text
output/mysql_import_logs/YYYY-MM_failed.json
```

## 数据语义

`value_resolved` 会保留原始 ID 字段，同时补充名称字段。例如：

- `custUserId` 保留联系人 ID，新增 `cust_user_name`。
- `servicerUserId` 保留客服 ID，新增 `servicer_user_name`。
- `servicerGroupId` 保留客服组 ID，新增 `servicer_group_name`。
- `ticketTemplateId` 保留模板 ID，新增 `ticket_template_name`。

这样既能追溯源系统主键，又能直接用于报表展示。

## 项目文档

- [项目接手手册](docs/project_handover_guide.md)
- [数据库设计与使用说明](docs/database_usage.md)
- [ERP 合并与分摊字段说明](docs/erp_merge/标准Sheet1与分摊字段说明.md)
- [工单 API 数据解析映射](docs/api_data_resolution_mapping.md)
- [人员数据入库说明](docs/personnel_mysql_usage.md)
- [时间指标使用说明](docs/time_metrics_usage.md)
- [生产运行、日志和备份说明](docs/production_operations.md)

## 注意事项

- `mysql-drop-tables` 会删除全部 5 张表，只能在明确确认目标库后使用。
- 默认分页大小是 `5000`；如果接口 500，降低 `--per-page`。
- 批量导入前建议先用单月、低并发参数试跑。
- MySQL 分区已包含 2025/2026 和 `pmax`，后续月份可通过 `mysql-add-partitions` 提前创建。
- 表结构、关联关系和常用查询以
  [数据库设计与使用说明](docs/database_usage.md) 为准。
