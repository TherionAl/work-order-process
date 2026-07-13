# work_order_process_v1.1：工单数据获取、解析与 MySQL 入库

本项目从“帮我吧”工单系统 API 获取工单数据，支持月度 JSON 导出、样本详情解析、数据字典中文化，以及面向 MySQL 数据湖的批量入库。

当前版本是组合版：以 Claude_code 版的 MySQL 数据湖能力为主线，同时保留根目录版本中更实用的轻量月度导出命令 `monthly-tickets` 和样本详情并发参数 `--detail-workers`。

## 核心能力

1. 按创建时间导出指定年份或月份的工单列表。
2. 生成三段式详情 JSON：`raw`、`value_resolved`、`chinese`。
3. 保留原始 ID，同时补充可读名称字段，便于入库后分析。
4. 支持 MySQL 5 表结构：工单主表、自定义字段明细、客户、联系人、同步日志。
5. 支持按月分区、未来分区创建、按月/按年批量导入、断点续跑和同步日志查看。

## 环境准备

```powershell
uv sync
```

配置优先级：

1. `.env`
2. `agents.md` 中的 `USERNAME`、`PASSWORD`、实际项目地址前缀

`.env` 示例：

```dotenv
WORKORDER_USERNAME=your_username
WORKORDER_PASSWORD=your_password
WORKORDER_BASE_URL=https://workorder.bosssoft.com.cn/api/v1

WORKORDER_MYSQL_HOST=127.0.0.1
WORKORDER_MYSQL_PORT=3306
WORKORDER_MYSQL_USER=root
WORKORDER_MYSQL_PASSWORD=your_mysql_password
WORKORDER_MYSQL_DATABASE=work_order_datalake
```

## 日常导出

导出 2025 年月度工单合集和每月样本详情：

```powershell
uv run work_order_process_v1.1 run
```

只跑指定月份：

```powershell
uv run work_order_process_v1.1 run --year 2026 --month 6
```

调试时限制每月最多拉取 10 条列表记录：

```powershell
uv run work_order_process_v1.1 run --year 2026 --limit-per-month 10 --overwrite
```

调整样本详情并发线程数：

```powershell
uv run work_order_process_v1.1 run --year 2026 --detail-workers 4
```

只导出月度工单合集，不拉取详情样本：

```powershell
uv run work_order_process_v1.1 monthly-tickets --year 2026
uv run work_order_process_v1.1 monthly-tickets --year 2026 --month 6
```

按工单模板抽样：

```powershell
uv run work_order_process_v1.1 template-samples --year 2026 --month 6 --sample-size 3 --seed 202606 --overwrite
```

探测认证和接口：

```powershell
uv run work_order_process_v1.1 probe
```

导出数据字典：

```powershell
uv run work_order_process_v1.1 dictionary
```

## MySQL 入库

服务器部署、调度、补录与对账说明见 [服务器运行手册](docs/server_operations.md)。

初始化 5 表结构和分区：

```powershell
uv run work_order_process_v1.1 mysql-init
```

导入单条工单：

```powershell
uv run work_order_process_v1.1 mysql-import-ticket --ticket-id 22256891
```

导入某个月：

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1
```

导入全年：

```powershell
uv run work_order_process_v1.1 mysql-import-year --year 2025
```

低速试跑建议：

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

导入客户和联系人：

```powershell
uv run work_order_process_v1.1 mysql-import-customers
uv run work_order_process_v1.1 mysql-import-contacts
```

提前创建未来分区：

```powershell
uv run work_order_process_v1.1 mysql-add-partitions --months-ahead 6
```

查看最近同步日志：

```powershell
uv run work_order_process_v1.1 mysql-sync-log --log-limit 20
```

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

## 注意事项

- `mysql-drop-tables` 会删除全部 5 张表，只能在明确确认目标库后使用。
- 默认分页大小是 `5000`；如果接口 500，降低 `--per-page`。
- 批量导入前建议先用单月、低并发参数试跑。
- MySQL 分区已包含 2025/2026 和 `pmax`，后续月份可通过 `mysql-add-partitions` 提前创建。
- 详细表结构见 `docs/mysql_schema.md`。
- 合并说明见 `docs/merged_practical_guide.md`。
