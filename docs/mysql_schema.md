# MySQL 数据库设计

更新时间：2026-07-06

## 设计目标

项目后续计划把工单、客户、联系人统一写入本机 MySQL，作为专门存取工单数据的数据仓库。

建议数据库：

```text
work_order
```

最终建议维护 5 张表：

```text
ticket_detail_main
ticket_detail_custom_fields
customers
contacts
sync_task_log
```

当前代码已经实现了 `ticket_detail_main` 和 `ticket_detail_custom_fields` 的基础版建表和单条工单入库；本文档记录的是后续应升级到的目标设计。

## 总体原则

1. 工单数据量最大，工单两张表都按月分区。
2. 客户/公司统一进 `customers`。
3. 联系人/公司联系人统一进 `contacts`。
4. 不建 `ticket_detail_raw`。
5. 不单独建附件表，附件类字段以 JSON 或文本保存在对应业务表。
6. 分区表不使用 MySQL 外键，由程序逻辑和索引保证关联。
7. 高频分析维度冗余到 `ticket_detail_main`，低频动态字段保留在 `ticket_detail_custom_fields`。

## 表 1：ticket_detail_main

定位：

- 工单唯一主表。
- 一条工单一行。
- 保存工单详情顶层字段。
- 冗余高频分析维度，便于按月、地区、模板、节点、客服等维度统计。

建议按 `create_dt` 月度分区。由于 MySQL 分区表要求所有唯一键都包含分区字段，主键建议包含 `create_dt`。

建议主键：

```sql
PRIMARY KEY (ticket_id, create_dt)
```

建议核心字段：

```text
ticket_id
subject
descript
cust_user_id
ticket_type
priority_level
ticket_status
ticket_source
ticket_template_id
create_dt
update_dt
solve_dt
open_dt
close_dt
source_updated_at
create_year
create_month
create_month_label
last_sync_at
sync_status
sync_error
```

建议人员/组织字段：

```text
contact_name
customer_id
customer_name
servicer_user_id
servicer_name
servicer_group_id
servicer_group_name
creater_id
creater_name
```

建议高频分析维度字段：

```text
province
city
district
region_text
product_line
module_name
problem_type
customer_type
customer_industry
department_id
department_name
current_node_name
current_node_status
current_node_started_at
current_node_duration_seconds
```

建议索引：

```sql
KEY idx_ticket_id (ticket_id),
KEY idx_create_month_label (create_month_label),
KEY idx_source_updated_at (source_updated_at),
KEY idx_last_sync_at (last_sync_at),
KEY idx_month_template (create_month_label, ticket_template_id),
KEY idx_month_status (create_month_label, ticket_status),
KEY idx_month_region (create_month_label, province, city, district),
KEY idx_month_problem_type (create_month_label, problem_type),
KEY idx_month_department (create_month_label, department_id)
```

## 表 2：ticket_detail_custom_fields

定位：

- 工单自定义字段明细表。
- 一条 `custom_fields` 字段一行。
- 用多行承接动态字段，避免形成超宽表。

这张表会比主表大很多，也建议按 `create_dt` 月度分区。

建议主键：

```sql
PRIMARY KEY (ticket_id, field_order, create_dt)
```

建议字段：

```text
ticket_id
ticket_template_id
create_dt
create_year
create_month
create_month_label
field_order
field_key
field_name
field_value
field_value_json
field_value_type
last_sync_at
```

字段说明：

- `field_key`：原始字段 key，例如 `field_1212`。
- `field_name`：转换后的中文字段名。
- `field_value`：普通文本值；数组或对象也可转成 JSON 字符串存这里，方便导出。
- `field_value_json`：数组或对象原结构对应的 JSON。
- `field_value_type`：`str`、`list`、`dict`、`null` 等。

建议索引：

```sql
KEY idx_ticket_id (ticket_id),
KEY idx_month_field (create_month_label, field_name),
KEY idx_month_template_field (create_month_label, ticket_template_id, field_name),
KEY idx_field_name (field_name),
KEY idx_field_key (field_key)
```

## 工单表分区方案

`ticket_detail_main` 和 `ticket_detail_custom_fields` 建议使用同一套按月分区方案：

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
  PARTITION pmax VALUES LESS THAN (MAXVALUE)
);
```

说明：

- 2025 年是历史备份数据，按月份导入和查询最自然。
- `pmax` 用于兜底接收 2026 年及以后的数据。
- 如果后续要持续同步 2026 年数据，可以继续补充 2026 年月度分区。

## 表 3：customers

定位：

- 客户/公司统一表。
- 接口里的“客户”和“公司”本质是同一类实体。

命名统一：

```text
客户 = 公司 = customers
```

建议普通表即可，暂不分区。

建议字段：

```text
customer_id
customer_name
customer_type
province
city
district
address
source_flags
source_updated_at
last_sync_at
created_at
updated_at
```

字段说明：

- `source_flags` 记录实体出现过的来源，例如 `customer,company`。
- 通过 `customer_id` 去重合并。

建议索引：

```sql
PRIMARY KEY (customer_id),
KEY idx_customer_name (customer_name),
KEY idx_customer_type (customer_type),
KEY idx_region (province, city, district),
KEY idx_source_flags (source_flags)
```

## 表 4：contacts

定位：

- 联系人/公司联系人统一表。
- 接口里的“联系人”和“公司联系人”本质是同一类实体。

命名统一：

```text
联系人 = 公司联系人 = contacts
```

建议普通表即可，暂不分区。

建议字段：

```text
contact_id
contact_name
phone
email
qq
wechat
customer_id
customer_name
department_name
position_name
source_flags
source_updated_at
last_sync_at
created_at
updated_at
```

字段说明：

- `source_flags` 记录实体出现过的来源，例如 `contact,company_contact`。
- `customer_id` 用于关联 `customers.customer_id`。
- 不建议强制 MySQL 外键，避免历史脏数据和批量导入受阻。

建议索引：

```sql
PRIMARY KEY (contact_id),
KEY idx_customer_id (customer_id),
KEY idx_contact_name (contact_name),
KEY idx_phone (phone),
KEY idx_source_flags (source_flags)
```

## 表 5：sync_task_log

定位：

- 同步任务日志表。
- 不是业务数据表。
- 用于记录每次批量同步或导入任务的执行情况。

建议字段：

```text
id
task_type
target_year
target_month
target_month_label
status
total_count
success_count
failed_count
skipped_count
started_at
finished_at
duration_seconds
error_message
extra_json
created_at
```

建议索引：

```sql
PRIMARY KEY (id),
KEY idx_task_month (task_type, target_month_label),
KEY idx_status (status),
KEY idx_started_at (started_at)
```

用途：

- 记录某个月是否跑过。
- 记录成功、失败、跳过数量。
- 记录任务是否中断。
- 保存失败 ID 列表或错误摘要。

## 关联关系

逻辑关联：

```text
ticket_detail_main.cust_user_id -> contacts.contact_id
contacts.customer_id -> customers.customer_id
ticket_detail_custom_fields.ticket_id -> ticket_detail_main.ticket_id
```

不建议强制建 MySQL 外键，原因：

- 工单两张表需要分区。
- MySQL 分区表不适合外键。
- 历史数据可能存在脏关联。
- 批量导入时外键会降低吞吐并增加失败概率。

## 工单更新策略

同步单条工单时：

1. 拉取接口详情。
2. 读取远端 `updateDT`，写入 `source_updated_at`。
3. 查询本地同一 `ticket_id` 的 `source_updated_at`。
4. 如果远端没有变化：
   - 可跳过主数据更新。
   - 记录 `skipped` 或只更新 `last_sync_at`。
5. 如果远端有变化：
   - upsert `ticket_detail_main`。
   - 删除该 `ticket_id` 的旧 `ticket_detail_custom_fields`。
   - 插入当前详情中的全部 `custom_fields`。

自定义字段采用整条重刷策略，避免字段新增、删除、顺序变化后留下旧数据。

## 当前代码状态

当前代码已实现：

- `mysql-init`：初始化基础版 `ticket_detail_main` 和 `ticket_detail_custom_fields`。
- `mysql-import-ticket --ticket-id 22256891`：拉取单条工单详情并入库。

已验证：

- `22256891` 主表写入 1 行。
- `22256891` 自定义字段写入 189 行。

待升级：

- 将当前基础 DDL 升级为本文档中的 5 表模型。
- 工单两张表改为分区表。
- 增加年月、同步、分析维度字段。
- 增加客户、联系人、同步日志表。
