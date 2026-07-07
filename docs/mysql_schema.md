# MySQL 数据库设计说明

本文档对应当前组合版代码中的 MySQL 实现，主要来源是：

- `src/work_order_process/mysql_storage.py`
- `src/work_order_process/resolver.py`
- `src/work_order_process/structured_ticket.py`
- `src/work_order_process/structured_entities.py`

当前版本已经不是早期“两张基础表”的设计，而是面向长期同步和统计分析的 5 表数据仓库模型。

## 1. 总体结构

默认数据库名：

```text
work_order_datalake
```

当前创建 5 张表：

```text
work_order_datalake
├── ticket_detail_main              工单详情主表，按 create_dt 月度分区
├── ticket_detail_custom_fields     工单自定义字段明细表，按 create_dt 月度分区
├── customers                       客户/公司统一表
├── contacts                        联系人/公司联系人统一表
└── sync_task_log                   同步任务日志表
```

设计重点：

1. 工单数据量最大，`ticket_detail_main` 和 `ticket_detail_custom_fields` 按月分区。
2. 分区表不使用 MySQL 外键，依靠程序逻辑和索引保证关联。
3. 工单主表保留原始 ID，同时补充可读名称字段。
4. 高频分析维度冗余到 `ticket_detail_main`，动态字段保留到 `ticket_detail_custom_fields`。
5. 客户/公司统一进入 `customers`。
6. 联系人/公司联系人统一进入 `contacts`。
7. 每次同步写入 `sync_task_log`，便于断点续跑、排查失败和统计耗时。

## 2. 表关系

逻辑关系如下：

```text
customers.customer_id
        ↑
        │ contacts.customer_id
contacts.contact_id
        ↑
        │ ticket_detail_main.cust_user_id
ticket_detail_main.ticket_id + create_dt
        ↑
        │ ticket_detail_custom_fields.ticket_id + create_dt
ticket_detail_custom_fields

sync_task_log 独立记录同步任务执行情况
```

注意：

- `ticket_detail_main.cust_user_id` 逻辑关联 `contacts.contact_id`。
- `contacts.customer_id` 逻辑关联 `customers.customer_id`。
- `ticket_detail_custom_fields.ticket_id` 逻辑关联 `ticket_detail_main.ticket_id`。
- 工单两张分区表不创建外键。

## 3. 分区方案

当前工单两张表使用同一套分区方案：

```sql
PARTITION BY RANGE COLUMNS(create_dt) (
  PARTITION p202501 VALUES LESS THAN ('2025-02-01'),
  PARTITION p202502 VALUES LESS THAN ('2025-03-01'),
  PARTITION p202503 VALUES LESS THAN ('2025-04-01'),
  PARTITION p202504 VALUES LESS THAN ('2025-05-01'),
  PARTITION p202505 VALUES LESS THAN ('2025-06-01'),
  PARTITION p202506 VALUES LESS THAN ('2025-07-01'),
  PARTITION p202507 VALUES LESS THAN ('2025-08-01'),
  PARTITION p202508 VALUES LESS THAN ('2025-09-01'),
  PARTITION p202509 VALUES LESS THAN ('2025-10-01'),
  PARTITION p202510 VALUES LESS THAN ('2025-11-01'),
  PARTITION p202511 VALUES LESS THAN ('2025-12-01'),
  PARTITION p202512 VALUES LESS THAN ('2026-01-01'),
  PARTITION p202601 VALUES LESS THAN ('2026-02-01'),
  PARTITION p202602 VALUES LESS THAN ('2026-03-01'),
  PARTITION p202603 VALUES LESS THAN ('2026-04-01'),
  PARTITION p202604 VALUES LESS THAN ('2026-05-01'),
  PARTITION p202605 VALUES LESS THAN ('2026-06-01'),
  PARTITION p202606 VALUES LESS THAN ('2026-07-01'),
  PARTITION p202607 VALUES LESS THAN ('2026-08-01'),
  PARTITION p202608 VALUES LESS THAN ('2026-09-01'),
  PARTITION p202609 VALUES LESS THAN ('2026-10-01'),
  PARTITION p202610 VALUES LESS THAN ('2026-11-01'),
  PARTITION p202611 VALUES LESS THAN ('2026-12-01'),
  PARTITION p202612 VALUES LESS THAN ('2027-01-01'),
  PARTITION pmax    VALUES LESS THAN (MAXVALUE)
)
```

后续月份通过命令创建：

```powershell
uv run work_order_process_v1.1 mysql-add-partitions --months-ahead 6
```

底层逻辑是重组 `pmax`：

```sql
ALTER TABLE ticket_detail_main
REORGANIZE PARTITION pmax INTO (
  PARTITION pYYYYMM VALUES LESS THAN ('YYYY-MM-01'),
  PARTITION pmax VALUES LESS THAN (MAXVALUE)
);
```

自定义字段表同步执行同样的分区重组。

## 4. 表 1：ticket_detail_main

### 4.1 用途

`ticket_detail_main` 是工单详情主表，一条工单一行。它保存工单详情接口返回的顶层字段、解析后的人员/模板名称、同步状态，以及从自定义字段抽取出的高频分析维度。

主键：

```sql
PRIMARY KEY (ticket_id, create_dt)
```

使用 `create_dt` 进入主键，是因为 MySQL 分区表要求所有唯一键包含分区字段。

### 4.2 主要字段

| 字段名 | 中文说明 | 来源/用途 |
|---|---|---|
| `ticket_id` | 工单 ID | API `ticketId`，主键之一。 |
| `subject` | 工单标题 | API `subject`。 |
| `descript` | 工单描述 | API `descript`。 |
| `cust_user_id` | 联系人 ID | API `custUserId`，保留原始 ID。 |
| `cust_user_name` | 联系人姓名 | resolver 调联系人详情接口补充。 |
| `servicer_user_id` | 客服 ID | API `servicerUserId`，保留原始 ID。 |
| `servicer_user_name` | 客服姓名 | resolver 调客服详情接口补充。 |
| `cc_user_id_list` | 抄送客服 ID/名称列表 | API `ccUserIdList`，解析后保存。 |
| `ticket_type` | 工单类型 | API `ticketType`，枚举值解析。 |
| `priority_level` | 优先级 | API `priorityLevel`，枚举值解析。 |
| `ticket_status` | 工单状态 | API `ticketStatus`，枚举值解析。 |
| `create_dt` | 创建时间 | API `createDT`，分区字段。 |
| `source_updated_at` | 来源更新时间 | API `updateDT`，用于增量判断。 |
| `solve_dt` | 解决时间 | API `solveDT`。 |
| `wait_dt` | 等待时间 | API `waitDT`。 |
| `open_dt` | 开启时间 | API `openDT`。 |
| `close_dt` | 关闭时间 | API `closeDT`。 |
| `servicer_group_id` | 客服组 ID | API `servicerGroupId`。 |
| `servicer_group_name` | 客服组名称 | resolver 调客服组详情接口补充。 |
| `creater_id` | 创建人 ID | API `createrId`。 |
| `creater_name` | 创建人姓名 | resolver 调客服详情接口补充。 |
| `ticket_template_id` | 工单模板 ID | API `ticketTemplateId`。 |
| `ticket_template_name` | 工单模板名称 | resolver 调模板详情接口补充。 |
| `current_node_field` | 当前流程节点字段 | API `currentNodeField`。 |
| `current_node_field_value` | 当前流程节点字段值 | API `currentNodeFieldValue`。 |
| `node_field_into_time` | 进入节点时间 | API `nodeFieldIntoTime`，秒级时间戳解析。 |
| `descript_attachments` | 描述附件 | JSON 字段。 |
| `create_year` | 创建年份 | 从 `create_dt` 派生。 |
| `create_month` | 创建月份 | 从 `create_dt` 派生。 |
| `create_month_label` | 创建年月 | `YYYY-MM`，从 `create_dt` 派生。 |
| `last_sync_at` | 最近同步时间 | 程序写入。 |
| `sync_status` | 同步状态 | `success`、`skipped`、`failed`。 |
| `sync_error` | 同步错误 | 失败时记录。 |

### 4.3 高频分析维度

以下字段由 resolver 从 `custom_fields` 或当前节点字段中抽取：

| 字段名 | 中文说明 | 来源/用途 |
|---|---|---|
| `province` | 省份 | 自定义字段中的省份/地区类字段。 |
| `city` | 城市 | 自定义字段中的城市/地区类字段。 |
| `district` | 区县 | 自定义字段中的区县/地区类字段。 |
| `region_text` | 地区文本 | 原始地区文本。 |
| `product_line` | 产品线 | 自定义字段抽取。 |
| `module_name` | 模块名称 | 自定义字段抽取。 |
| `problem_type` | 问题类型 | 自定义字段抽取。 |
| `customer_type` | 客户类型 | 自定义字段或公司字段抽取。 |
| `customer_industry` | 客户行业 | 自定义字段或公司字段抽取。 |
| `department_id` | 部门 ID | 后续可从人员或字段补充。 |
| `department_name` | 部门名称 | 后续可从人员或字段补充。 |
| `current_node_name` | 当前节点名称 | 当前流程节点字段解析。 |
| `current_node_status` | 当前节点状态 | 当前流程节点字段解析。 |
| `current_node_started_at` | 当前节点进入时间 | 来自 `nodeFieldIntoTime`。 |
| `current_node_duration_seconds` | 当前节点停留秒数 | 后续可计算。 |

### 4.4 索引

当前主表索引：

```sql
PRIMARY KEY (ticket_id, create_dt),
KEY idx_create_month_label (create_month_label),
KEY idx_source_updated_at (source_updated_at),
KEY idx_last_sync_at (last_sync_at),
KEY idx_ticket_template_id (ticket_template_id),
KEY idx_ticket_status (ticket_status),
KEY idx_month_template (create_month_label, ticket_template_id),
KEY idx_month_status (create_month_label, ticket_status),
KEY idx_month_region (create_month_label, province, city, district),
KEY idx_month_problem_type (create_month_label, problem_type),
KEY idx_month_department (create_month_label, department_id)
```

查询建议：

- 月度查询优先用 `create_dt` 范围，便于分区裁剪。
- 报表统计可用 `create_month_label`。
- 按模板、状态、地区、问题类型统计时使用复合索引。

## 5. 表 2：ticket_detail_custom_fields

### 5.1 用途

`ticket_detail_custom_fields` 保存工单详情中的 `custom_fields` 数组。一条自定义字段一行，用来承接不同工单模板下差异很大的动态字段。

主键：

```sql
PRIMARY KEY (id, create_dt)
```

唯一键：

```sql
UNIQUE KEY uk_ticket_field_order (ticket_id, field_order, create_dt)
```

### 5.2 字段

| 字段名 | 中文说明 | 来源/用途 |
|---|---|---|
| `id` | 自增主键 | 分区表主键组成之一。 |
| `ticket_id` | 工单 ID | 逻辑关联主表。 |
| `ticket_template_id` | 工单模板 ID | 冗余，便于按模板查字段。 |
| `create_dt` | 工单创建时间 | 分区字段，来自主表。 |
| `create_year` | 创建年份 | 从 `create_dt` 派生。 |
| `create_month` | 创建月份 | 从 `create_dt` 派生。 |
| `create_month_label` | 创建年月 | `YYYY-MM`。 |
| `field_order` | 字段顺序 | `custom_fields` 数组顺序，从 1 开始。 |
| `field_key` | 原始字段 key | 例如 `field_1212`。 |
| `field_name` | 字段中文名 | resolver 根据字段接口解析。 |
| `field_value` | 字段值文本 | 普通值或 JSON 字符串。 |
| `field_value_json` | 字段值 JSON | 数组/对象时保存原结构。 |
| `field_value_type` | 字段值类型 | `str`、`list`、`dict`、`null` 等。 |
| `last_sync_at` | 最近同步时间 | 程序写入。 |
| `created_at` | 入库时间 | MySQL 默认值。 |
| `updated_at` | 更新时间 | MySQL 自动更新。 |

### 5.3 索引

```sql
PRIMARY KEY (id, create_dt),
UNIQUE KEY uk_ticket_field_order (ticket_id, field_order, create_dt),
KEY idx_ticket_id (ticket_id),
KEY idx_month_field (create_month_label, field_name),
KEY idx_month_template_field (create_month_label, ticket_template_id, field_name),
KEY idx_field_name (field_name),
KEY idx_field_key (field_key)
```

设计说明：

- `ticket_id + field_order + create_dt` 保证同一工单同一字段顺序唯一。
- 自定义字段采用整票重刷策略，避免字段新增、删除、顺序变化后旧字段残留。
- `create_dt` 冗余到明细表，避免月度统计时频繁 join 主表。

## 6. 表 3：customers

### 6.1 用途

`customers` 合并客户和公司两类实体。因为业务上客户、公司都可以作为工单关联主体，合并后更便于统计。

主键：

```sql
PRIMARY KEY (customer_id)
```

### 6.2 字段

| 字段名 | 中文说明 | 来源/用途 |
|---|---|---|
| `customer_id` | 客户/公司 ID | API 主键。 |
| `customer_name` | 客户/公司名称 | 主要展示字段。 |
| `customer_type` | 客户类型 | 公司字段或接口字段。 |
| `province` | 省份 | 地址/地区字段。 |
| `city` | 城市 | 地址/地区字段。 |
| `district` | 区县 | 地址/地区字段。 |
| `address` | 地址 | API 地址字段。 |
| `source_flags` | 来源标记 | `customer`、`company` 或组合。 |
| `source_updated_at` | 来源更新时间 | API 更新时间。 |
| `last_sync_at` | 最近同步时间 | 程序写入。 |
| `created_at` | 入库时间 | MySQL 默认值。 |
| `updated_at` | 更新时间 | MySQL 自动更新。 |

### 6.3 索引

```sql
PRIMARY KEY (customer_id),
KEY idx_customer_name (customer_name),
KEY idx_customer_type (customer_type),
KEY idx_region (province, city, district),
KEY idx_source_flags (source_flags)
```

## 7. 表 4：contacts

### 7.1 用途

`contacts` 合并联系人和公司联系人。工单主表的 `cust_user_id` 逻辑关联到 `contacts.contact_id`。

主键：

```sql
PRIMARY KEY (contact_id)
```

### 7.2 字段

| 字段名 | 中文说明 | 来源/用途 |
|---|---|---|
| `contact_id` | 联系人 ID | API 主键。 |
| `contact_name` | 联系人姓名 | 主要展示字段。 |
| `phone` | 手机号 | 联系方式。 |
| `email` | 邮箱 | 联系方式。 |
| `qq` | QQ | 联系方式。 |
| `wechat` | 微信 | 联系方式。 |
| `customer_id` | 所属客户/公司 ID | 逻辑关联 customers。 |
| `customer_name` | 所属客户/公司名称 | 冗余展示字段。 |
| `department_name` | 联系人部门 | API 字段。 |
| `position_name` | 联系人职位 | API 字段。 |
| `source_flags` | 来源标记 | `contact`、`company_contact` 或组合。 |
| `source_updated_at` | 来源更新时间 | API 更新时间。 |
| `last_sync_at` | 最近同步时间 | 程序写入。 |
| `created_at` | 入库时间 | MySQL 默认值。 |
| `updated_at` | 更新时间 | MySQL 自动更新。 |

### 7.3 索引

```sql
PRIMARY KEY (contact_id),
KEY idx_customer_id (customer_id),
KEY idx_contact_name (contact_name),
KEY idx_phone (phone),
KEY idx_source_flags (source_flags)
```

## 8. 表 5：sync_task_log

### 8.1 用途

`sync_task_log` 记录每次同步任务执行情况，包括任务类型、目标年月、成功数、失败数、跳过数、耗时和扩展 JSON。

### 8.2 字段

| 字段名 | 中文说明 | 来源/用途 |
|---|---|---|
| `id` | 自增主键 | 日志 ID。 |
| `task_type` | 任务类型 | `ticket_detail`、`customer`、`contact`。 |
| `target_year` | 目标年份 | 任务参数。 |
| `target_month` | 目标月份 | 任务参数。 |
| `target_month_label` | 目标年月 | `YYYY-MM`。 |
| `status` | 任务状态 | `running`、`success`、`failed`、`partial`。 |
| `total_count` | 应处理数量 | 任务统计。 |
| `success_count` | 成功数量 | 任务统计。 |
| `failed_count` | 失败数量 | 任务统计。 |
| `skipped_count` | 跳过数量 | `source_updated_at` 未变化时跳过。 |
| `started_at` | 开始时间 | MySQL 默认值或程序写入。 |
| `finished_at` | 结束时间 | 程序写入。 |
| `duration_seconds` | 耗时秒数 | 程序计算。 |
| `error_message` | 错误摘要 | 失败时写入。 |
| `extra_json` | 扩展信息 | 失败 ID、checkpoint 等。 |
| `created_at` | 日志创建时间 | MySQL 默认值。 |

### 8.3 索引

```sql
PRIMARY KEY (id),
KEY idx_task_month (task_type, target_month_label),
KEY idx_status (status),
KEY idx_started_at (started_at)
```

## 9. 入库和更新策略

### 9.1 单条工单

流程：

1. 调用工单详情接口获取 raw detail。
2. 调用 resolver 解析枚举、人员、客服组、模板、自定义字段。
3. 构造 `ticket_detail_main` 一行。
4. 构造 `ticket_detail_custom_fields` 多行。
5. 在同一个事务中执行主表 upsert 和自定义字段刷新。

命令：

```powershell
uv run work_order_process_v1.1 mysql-import-ticket --ticket-id 22256891
```

### 9.2 按月导入

流程：

1. 按 `createDT:YYYY-MM` 搜索该月工单。
2. 分页获取所有工单 ID。
3. 并发拉取详情。
4. 按批次写入 MySQL。
5. 记录 `sync_task_log`。

命令：

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1
```

低并发试跑：

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

### 9.3 按年导入

命令：

```powershell
uv run work_order_process_v1.1 mysql-import-year --year 2025
```

也可以只指定其中一个月份：

```powershell
uv run work_order_process_v1.1 mysql-import-year --year 2025 --month 6
```

### 9.4 增量跳过

`_upsert_ticket_detail` 会先查询现有行的 `source_updated_at`：

```sql
SELECT source_updated_at
FROM ticket_detail_main
WHERE ticket_id = ? AND create_dt = ?;
```

如果远端 `updateDT` 与本地 `source_updated_at` 一致：

- 不重写主表。
- 不刷新自定义字段。
- 只更新 `last_sync_at` 和 `sync_status='skipped'`。

如果不存在或更新时间变化：

- 主表执行 `INSERT ... ON DUPLICATE KEY UPDATE`。
- 自定义字段先删除同一 `ticket_id + create_dt` 的旧行。
- 再批量插入新的自定义字段行。

## 10. 常用查询

### 10.1 查看月度工单量

```sql
SELECT
  create_month_label,
  COUNT(*) AS ticket_count
FROM ticket_detail_main
WHERE create_month_label BETWEEN '2025-01' AND '2025-12'
GROUP BY create_month_label
ORDER BY create_month_label;
```

### 10.2 按 create_dt 范围查询，利用分区裁剪

```sql
SELECT ticket_id, subject, ticket_status, create_dt
FROM ticket_detail_main
WHERE create_dt >= '2025-06-01'
  AND create_dt < '2025-07-01'
ORDER BY create_dt;
```

### 10.3 验证分区裁剪

```sql
EXPLAIN PARTITIONS
SELECT ticket_id
FROM ticket_detail_main
WHERE create_dt >= '2025-06-01'
  AND create_dt < '2025-07-01';
```

预期 `partitions` 只出现 `p202506`。

### 10.4 查询单条工单详情

```sql
SELECT *
FROM ticket_detail_main
WHERE ticket_id = 22256891;
```

### 10.5 查询单条工单自定义字段

```sql
SELECT
  field_order,
  field_key,
  field_name,
  field_value,
  field_value_type
FROM ticket_detail_custom_fields
WHERE ticket_id = 22256891
ORDER BY create_dt, field_order;
```

### 10.6 查看某个自定义字段值分布

```sql
SELECT
  field_value,
  COUNT(DISTINCT ticket_id) AS ticket_count
FROM ticket_detail_custom_fields
WHERE create_month_label BETWEEN '2025-01' AND '2025-12'
  AND field_name = '客户性质'
GROUP BY field_value
ORDER BY ticket_count DESC;
```

### 10.7 按模板查看字段清单

```sql
SELECT
  ticket_template_id,
  field_name,
  COUNT(*) AS row_count,
  COUNT(DISTINCT ticket_id) AS ticket_count
FROM ticket_detail_custom_fields
WHERE create_month_label = '2025-06'
GROUP BY ticket_template_id, field_name
ORDER BY ticket_template_id, ticket_count DESC;
```

### 10.8 按客服统计工单量

```sql
SELECT
  servicer_user_id,
  servicer_user_name,
  COUNT(*) AS ticket_count
FROM ticket_detail_main
WHERE create_month_label BETWEEN '2025-01' AND '2025-12'
GROUP BY servicer_user_id, servicer_user_name
ORDER BY ticket_count DESC;
```

### 10.9 按客服组统计工单量

```sql
SELECT
  servicer_group_id,
  servicer_group_name,
  COUNT(*) AS ticket_count
FROM ticket_detail_main
WHERE create_month_label BETWEEN '2025-01' AND '2025-12'
GROUP BY servicer_group_id, servicer_group_name
ORDER BY ticket_count DESC;
```

### 10.10 按地区统计

```sql
SELECT
  province,
  city,
  district,
  COUNT(*) AS ticket_count
FROM ticket_detail_main
WHERE create_month_label BETWEEN '2025-01' AND '2025-12'
GROUP BY province, city, district
ORDER BY ticket_count DESC;
```

### 10.11 按问题类型统计

```sql
SELECT
  problem_type,
  COUNT(*) AS ticket_count
FROM ticket_detail_main
WHERE create_month_label BETWEEN '2025-01' AND '2025-12'
GROUP BY problem_type
ORDER BY ticket_count DESC;
```

### 10.12 统计解决时长

```sql
SELECT
  ticket_id,
  subject,
  create_dt,
  solve_dt,
  ROUND(TIMESTAMPDIFF(MINUTE, create_dt, solve_dt) / 60, 2) AS solve_hours
FROM ticket_detail_main
WHERE create_dt IS NOT NULL
  AND solve_dt IS NOT NULL
ORDER BY solve_hours DESC
LIMIT 100;
```

### 10.13 月度平均解决时长

```sql
SELECT
  create_month_label,
  COUNT(*) AS solved_count,
  ROUND(AVG(TIMESTAMPDIFF(MINUTE, create_dt, solve_dt)) / 60, 2) AS avg_solve_hours
FROM ticket_detail_main
WHERE create_month_label BETWEEN '2025-01' AND '2025-12'
  AND solve_dt IS NOT NULL
GROUP BY create_month_label
ORDER BY create_month_label;
```

### 10.14 查询失败或异常同步记录

```sql
SELECT
  task_type,
  target_month_label,
  status,
  total_count,
  success_count,
  failed_count,
  skipped_count,
  error_message,
  started_at,
  finished_at,
  duration_seconds
FROM sync_task_log
WHERE status IN ('failed', 'partial')
ORDER BY started_at DESC
LIMIT 50;
```

### 10.15 查看最近同步日志

```sql
SELECT
  id,
  task_type,
  target_month_label,
  status,
  total_count,
  success_count,
  failed_count,
  skipped_count,
  duration_seconds,
  started_at,
  finished_at
FROM sync_task_log
ORDER BY id DESC
LIMIT 20;
```

命令行等价：

```powershell
uv run work_order_process_v1.1 mysql-sync-log --log-limit 20
```

## 11. 运维建议

### 11.1 初始化

```powershell
uv run work_order_process_v1.1 mysql-init
```

该命令会：

1. 创建数据库。
2. 创建 5 张表。
3. 为两张工单表创建 2025-01 到 2026-12 的月度分区和 `pmax`。

### 11.2 全量导入前试跑

先低并发跑一个月：

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

检查：

- `sync_task_log` 是否记录成功。
- `ticket_detail_main` 行数是否接近接口返回数量。
- `ticket_detail_custom_fields` 是否有明细。
- 常用字段如 `servicer_user_name`、`ticket_template_name` 是否补齐。

### 11.3 正式导入

```powershell
uv run work_order_process_v1.1 mysql-import-year --year 2025 --max-workers 8 --batch-size 100 --api-rate-limit 10
```

如果接口限流或失败率较高：

- 降低 `--max-workers`。
- 降低 `--api-rate-limit`。
- 降低 `--batch-size`。
- 按月重跑失败月份。

### 11.4 分区维护

每月或每季度提前创建未来分区：

```powershell
uv run work_order_process_v1.1 mysql-add-partitions --months-ahead 6
```

### 11.5 危险操作

```powershell
uv run work_order_process_v1.1 mysql-drop-tables
```

该命令会删除全部 5 张表，只能在明确确认 `.env` 指向测试库或目标库后使用。

## 12. 与旧根目录版的差异

旧根目录版文档中有不少“目标版建议”内容；当前组合版已经把其中大部分目标落地：

| 项目 | 旧根目录版 | 当前组合版 |
|---|---|---|
| 工单主表 | 基础字段，后续建议补充分区和同步字段 | 已按 `create_dt` 分区，主键 `(ticket_id, create_dt)` |
| 自定义字段表 | 基础字段，后续建议补充 `create_dt` | 已包含 `create_dt`、年月字段和分区 |
| 客户表 | 文档建议 | 已落地 `customers` |
| 联系人表 | 文档建议 | 已落地 `contacts` |
| 同步日志 | 文档建议 | 已落地 `sync_task_log` |
| 原始 ID 与名称 | 建议同时保留 | 已保留 ID，并新增 `*_name` 字段 |
| 分区维护 | 建议方案 | 已提供 `mysql-add-partitions` |

因此当前文档不再使用“基础版/目标版”的旧描述，而是直接描述当前实现。
