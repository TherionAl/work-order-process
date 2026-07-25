# work_order_process：工单数据获取、解析与 MySQL 入库

本项目从“帮我吧”工单系统 API 获取工单数据，支持月度 JSON 导出、样本详情解析、数据字典中文化，以及面向 MySQL 数据湖的批量入库。

当前版本是组合版：以 Claude_code 版的 MySQL 数据湖能力为主线，同时保留根目录版本中更实用的轻量月度导出命令 `monthly-tickets` 和样本详情并发参数 `--detail-workers`。

## 核心能力

1. 按创建时间导出指定年份或月份的工单列表。
2. 生成三段式详情 JSON：`raw`、`value_resolved`、`chinese`。
3. 保留原始 ID，同时补充可读名称字段，便于入库后分析。
4. 支持工单、客户、联系人、ERP、台账及营收汇总数据统一进入 MySQL 数据湖。
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

提前创建未来分区：

```powershell
uv run work_order_process mysql-add-partitions --months-ahead 6
```

查看最近同步日志：

```powershell
uv run work_order_process mysql-sync-log --log-limit 20
```

## ERP、台账和营收

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
