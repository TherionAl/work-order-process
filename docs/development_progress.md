# 工单数据处理项目开发进度总结

更新时间：2026-07-07

当前项目目录：

```text
D:\Users\python_project\work_order_process_v1.1
```

当前版本是组合版：

- 以 Claude_code 版的 MySQL 数据湖能力为主线。
- 保留根目录版中更实用的 `monthly-tickets`、`--detail-workers`、API 缓存防御性拷贝和结构化测试。
- 文档已按根目录版的详细说明风格重新整理，并修正为当前组合版实际实现。

## 1. 项目目标

本项目用于从帮我吧工单系统 API 获取工单、客户、联系人等数据，并完成：

1. 工单按月份获取和本地 JSON 备份。
2. 工单详情字段值解析和中文化。
3. 工单详情结构化。
4. MySQL 数据湖入库。
5. 按月/按年批量同步。
6. 同步日志、分区维护和后续报表分析支撑。

当前接口认证方式为 HTTP Basic Auth，账号、密码和接口地址优先从 `.env` 读取，其次可从 `agents.md` 读取。

## 2. 当前已实现能力

### 2.1 月度工单列表导出

已确认工单搜索接口支持按创建月份查询：

```text
GET /tickets/search.json?query=createDT:YYYY-MM
```

导出月度工单合集和每月样本详情：

```powershell
uv run work_order_process_v1.1 run --year 2026
```

只导出月度工单列表，不拉取详情样本：

```powershell
uv run work_order_process_v1.1 monthly-tickets --year 2026
uv run work_order_process_v1.1 monthly-tickets --year 2026 --month 6
```

输出目录：

```text
output/YYYY_monthly_tickets/
```

每个月文件：

```text
YYYY-MM_tickets.json
```

文件包含：

- `month`
- `declared_count`
- `fetched_count`
- `ticket_ids`
- `tickets`

### 2.2 月度样本详情导出

项目会从每个月工单中按固定随机种子抽样，生成三段式 JSON：

```text
output/YYYY_monthly_sample_details/
  YYYY-MM_sample_details_raw.json
  YYYY-MM_sample_details_value_resolved.json
  YYYY-MM_sample_details_chinese.json
```

含义：

- `raw`：工单详情接口原始返回。
- `value_resolved`：英文 key 保留，解析枚举、人员、模板、自定义字段选项等 value。
- `chinese`：基于 `value_resolved`，再用数据字典把 key 中文化。

组合版保留 `--detail-workers`：

```powershell
uv run work_order_process_v1.1 run --year 2026 --detail-workers 4
```

### 2.3 按模板抽样

已实现按月份和模板组合查询：

```text
GET /tickets/search.json?query=createDT:YYYY-MM ticketTemplateId:<模板ID>
```

命令：

```powershell
uv run work_order_process_v1.1 template-samples --year 2026 --month 6 --sample-size 3 --seed 202606 --overwrite
```

输出目录：

```text
output/YYYY_MM_template_sample_details/
```

### 2.4 单条工单详情 Excel 结构化导出

脚本：

```powershell
.\.venv\Scripts\python.exe scripts\export_ticket_detail_excel.py 22256891 --output output\ticket_22256891_structured.xlsx
```

Excel 包含两个 sheet：

- `ticket_detail_main`
- `ticket_detail_custom_fields`

组合版继续保留根目录版的结构化模块：

```text
src/work_order_process/structured_ticket.py
src/work_order_process/structured_entities.py
```

用途：

- 统一工单顶层字段映射。
- 统一 `custom_fields` 明细行构造。
- 支持 Excel 导出和测试复用。

### 2.5 MySQL 5 表数据湖

当前 MySQL 模型已经落地，不再只是设计建议。

默认数据库：

```text
work_order_datalake
```

当前 5 张表：

```text
ticket_detail_main
ticket_detail_custom_fields
customers
contacts
sync_task_log
```

其中：

- `ticket_detail_main`：按 `create_dt` 月度分区，主键 `(ticket_id, create_dt)`。
- `ticket_detail_custom_fields`：按 `create_dt` 月度分区，保存动态字段明细。
- `customers`：客户/公司统一表。
- `contacts`：联系人/公司联系人统一表。
- `sync_task_log`：同步任务日志。

详细字段和索引见：

```text
docs/mysql_schema.md
```

### 2.6 MySQL 命令

| 命令 | 说明 |
|---|---|
| `mysql-init` | 创建数据库和 5 张表，包含 2025/2026 月度分区和 `pmax`。 |
| `mysql-drop-tables` | 删除全部 5 张表，危险命令。 |
| `mysql-import-ticket --ticket-id <id>` | 单条工单入库。 |
| `mysql-import-month --year YYYY --month MM` | 单月全部工单入库。 |
| `mysql-import-month-v1 --year YYYY --month MM` | 串行旧导入方式，用于调试对比。 |
| `mysql-import-year --year YYYY` | 全年工单入库。 |
| `mysql-import-customers` | 导入客户/公司到 `customers`。 |
| `mysql-import-contacts` | 导入联系人/公司联系人到 `contacts`。 |
| `mysql-add-partitions --months-ahead N` | 提前创建未来 N 个月分区。 |
| `mysql-sync-log --log-limit N` | 查看最近同步日志。 |

低并发试跑建议：

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

### 2.7 解析器语义

组合版采用适合数据库分析的语义：

- 保留原始 ID。
- 新增可读名称字段。
- 自定义字段值尽量转为可读文本。
- 高频分析维度从自定义字段抽取到主表。

示例：

| 原始字段 | 当前处理 |
|---|---|
| `custUserId` | 保留 ID，新增 `cust_user_name`。 |
| `servicerUserId` | 保留 ID，新增 `servicer_user_name`。 |
| `createrId` | 保留 ID，新增 `creater_name`。 |
| `servicerGroupId` | 保留 ID，新增 `servicer_group_name`。 |
| `ticketTemplateId` | 保留 ID，新增 `ticket_template_name`。 |

这和根目录旧版中“直接把 ID 替换成中文值”的部分描述不同。当前做法更适合长期数据仓库。

### 2.8 性能和体验优化

已实现：

1. API 实体详情缓存
   - 联系人、公司、客服、客服组、模板详情会在单次运行中缓存。
   - 组合版修复为返回防御性深拷贝，调用方修改结果不会污染缓存。

2. 数据字典 PDF 缓存
   - 首次解析 PDF 后生成 `.parsed.json`。
   - PDF 未变化时直接读取缓存。

3. 月度导出进度条
   - 年度/月度导出时显示当前月份和进度。

4. 样本详情并发
   - `--detail-workers` 控制样本详情拉取线程数。

5. MySQL 批量导入并发
   - `--max-workers` 控制 API 详情抓取并发。
   - `--batch-size` 控制每批提交量。
   - `--api-rate-limit` 控制 API QPS。

6. 同步日志
   - 每次按月/按年同步会写入 `sync_task_log`。

## 3. 当前验证结果

组合版本地测试：

```powershell
uv run pytest -q
```

结果：

```text
44 passed
```

已验证 CLI help 包含：

```text
run
monthly-tickets
template-samples
mysql-init
mysql-drop-tables
mysql-import-ticket
mysql-import-month
mysql-import-month-v1
mysql-import-year
mysql-import-customers
mysql-import-contacts
mysql-add-partitions
mysql-sync-log
```

已验证参数包含：

```text
--detail-workers
--max-workers
--batch-size
--api-rate-limit
--months-ahead
--log-limit
```

说明：

- 当前轮没有实际连接真实 API 和 MySQL 执行入库，避免影响你的数据库状态。
- 后续正式导入前，建议先用测试库低并发试跑一个月。

## 4. 当前文档状态

已按根目录版详细风格检查并更新：

| 文档 | 状态 |
|---|---|
| `README.md` | 已改为组合版使用说明，覆盖导出、入库、分区、注意事项。 |
| `docs/api_data_resolution_mapping.md` | 已改为当前组合版解析语义，修正“保留 ID + 新增名称字段”。 |
| `docs/mysql_schema.md` | 已改为当前 5 表分区实现的详细 schema 文档。 |
| `docs/development_progress.md` | 当前文件，已改为组合版实际进度。 |
| `docs/merged_practical_guide.md` | 新增合并说明和推荐实操流程。 |
| `agents.md` | 与根目录版一致，未改动。 |

## 5. 推荐实操流程

### 5.1 配置和接口探测

```powershell
uv sync
uv run work_order_process_v1.1 probe
```

### 5.2 先轻量导出一个月列表

```powershell
uv run work_order_process_v1.1 monthly-tickets --year 2026 --month 6 --overwrite
```

### 5.3 再导出一个月样本详情

```powershell
uv run work_order_process_v1.1 run --year 2026 --month 6 --limit-per-month 20 --detail-workers 2 --overwrite
```

### 5.4 初始化测试库

```powershell
uv run work_order_process_v1.1 mysql-init
```

### 5.5 低并发试跑一个月入库

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

### 5.6 查看同步日志

```powershell
uv run work_order_process_v1.1 mysql-sync-log --log-limit 20
```

### 5.7 放大到全年

确认单月结果正常后再运行：

```powershell
uv run work_order_process_v1.1 mysql-import-year --year 2025 --max-workers 8 --batch-size 100 --api-rate-limit 10
```

## 6. 后续开发计划

### 6.1 MySQL 集成验证

待在真实测试库验证：

- `mysql-init`
- `mysql-import-ticket`
- `mysql-import-month`
- `mysql-import-customers`
- `mysql-import-contacts`
- `mysql-add-partitions`
- `mysql-sync-log`

重点核对：

- `ticket_detail_main` 行数。
- `ticket_detail_custom_fields` 明细行数。
- `source_updated_at` 跳过策略。
- `sync_task_log` 统计结果。
- 分区裁剪是否生效。

### 6.2 模块拆分

当前 `mysql_storage.py` 功能较集中，后续可以拆分：

```text
mysql_schema.py        DDL 和分区
mysql_import.py        工单导入
mysql_entities.py      客户和联系人导入
mysql_sync_log.py      同步日志
mysql_rows.py          行构建
```

### 6.3 分析维度增强

根据实际报表需求继续补充：

- 地区清洗。
- 产品线归一。
- 模块名归一。
- 问题类型归一。
- 客服部门映射。
- 节点流转历史。

可能新增：

```text
ticket_node_history
dim_region
dim_department
support_department_map
```

### 6.4 测试增强

建议增加：

- MySQL DDL 生成测试。
- 分区月份生成测试。
- sync_task_log 写入测试。
- 客户/联系人行构建测试。
- 批量导入失败恢复测试。

## 7. 当前结论

组合版已经适合作为后续主线：

- 轻量 JSON 导出能力保留。
- MySQL 数据湖能力已合入。
- 文档已按根目录版详细风格更新。
- 测试已通过。

正式跑大量数据前，仍建议先在测试库低并发验证一个月数据。 
