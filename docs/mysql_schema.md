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

字段中文说明：

| 字段名 | 中文说明 | 备注 |
|---|---|---|
| `ticket_id` | 工单 ID | 工单唯一标识。 |
| `subject` | 工单标题 | 顶层字段 `subject`。 |
| `descript` | 工单描述 | 顶层字段 `descript`。 |
| `cust_user_id` | 联系人 ID | 对应接口字段 `custUserId`，逻辑关联 `contacts.contact_id`。 |
| `contact_name` | 联系人姓名 | 从联系人接口或 value 替换结果补充。 |
| `customer_id` | 客户/公司 ID | 由联系人或公司信息补充，逻辑关联 `customers.customer_id`。 |
| `customer_name` | 客户/公司名称 | 高频查询维度。 |
| `ticket_type` | 工单类型 | 例如问题、事务、故障、任务。 |
| `priority_level` | 优先级 | 例如低、正常、高、紧急。 |
| `ticket_status` | 工单状态 | 例如新建、已解决、已关闭。 |
| `ticket_source` | 工单来源 | 来源枚举暂保留接口值或解析值。 |
| `ticket_template_id` | 工单模板 | 可存模板 ID 或已解析模板名称，后续建议拆 ID/名称。 |
| `create_dt` | 工单创建时间 | 分区字段。 |
| `update_dt` | 工单修改时间 | 接口顶层字段。 |
| `solve_dt` | 工单解决时间 | 接口顶层字段。 |
| `open_dt` | 工单开启时间 | 接口顶层字段。 |
| `close_dt` | 工单关闭时间 | 接口顶层字段。 |
| `source_updated_at` | 来源更新时间 | 来自接口 `updateDT`，用于判断是否需要重刷。 |
| `create_year` | 创建年份 | 从 `create_dt` 派生。 |
| `create_month` | 创建月份 | 从 `create_dt` 派生。 |
| `create_month_label` | 创建年月 | 格式 `YYYY-MM`，用于按月查询。 |
| `servicer_user_id` | 客服 ID | 顶层字段 `servicerUserId`。 |
| `servicer_name` | 客服姓名 | 从客服详情接口补充。 |
| `servicer_group_id` | 客服组 ID | 顶层字段 `servicerGroupId`。 |
| `servicer_group_name` | 客服组名称 | 从客服组详情接口补充。 |
| `creater_id` | 创建人 ID | 顶层字段 `createrId`。 |
| `creater_name` | 创建人姓名 | 从客服或联系人接口补充。 |
| `province` | 省份 | 高频分析维度，可从客户或自定义字段抽取。 |
| `city` | 城市 | 高频分析维度。 |
| `district` | 区县 | 高频分析维度。 |
| `region_text` | 地区文本 | 无法拆省市区时保留原始地区文本。 |
| `product_line` | 产品线 | 从自定义字段抽取。 |
| `module_name` | 模块名称 | 从自定义字段抽取。 |
| `problem_type` | 问题类型 | 从自定义字段抽取。 |
| `customer_type` | 客户性质/类型 | 从公司字段或自定义字段抽取。 |
| `customer_industry` | 客户行业 | 后续客户分析维度。 |
| `department_id` | 内部部门 ID | 后续内部部门映射使用。 |
| `department_name` | 内部部门名称 | 后续按部门统计使用。 |
| `current_node_name` | 当前节点名称 | 从当前流程节点字段解析。 |
| `current_node_status` | 当前节点状态 | 从当前流程节点值解析。 |
| `current_node_started_at` | 当前节点进入时间 | 由 `nodeFieldIntoTime` 转换。 |
| `current_node_duration_seconds` | 当前节点停留秒数 | 可由当前时间或流转数据计算。 |
| `last_sync_at` | 最近同步时间 | 本地同步时间。 |
| `sync_status` | 同步状态 | 例如 `success`、`failed`、`skipped`。 |
| `sync_error` | 同步错误信息 | 记录最近一次同步错误摘要。 |

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

字段中文说明：

| 字段名 | 中文说明 | 备注 |
|---|---|---|
| `ticket_id` | 工单 ID | 逻辑关联 `ticket_detail_main.ticket_id`。 |
| `ticket_template_id` | 工单模板 | 冗余字段，便于按模板查自定义字段。 |
| `create_dt` | 工单创建时间 | 分区字段，来自主表。 |
| `create_year` | 创建年份 | 从 `create_dt` 派生。 |
| `create_month` | 创建月份 | 从 `create_dt` 派生。 |
| `create_month_label` | 创建年月 | 格式 `YYYY-MM`。 |
| `field_order` | 字段顺序 | `custom_fields` 中的顺序，从 1 开始。 |
| `field_key` | 原始字段 key | 例如 `field_1212`、`record_serviceruserid`。 |
| `field_name` | 中文字段名 | 由工单字段接口或解析逻辑得到。 |
| `field_value` | 字段值文本 | 普通值直接保存；数组/对象也可转 JSON 字符串保存。 |
| `field_value_json` | 字段值 JSON | 数组或对象原结构。 |
| `field_value_type` | 字段值类型 | 例如 `str`、`list`、`dict`、`null`。 |
| `last_sync_at` | 最近同步时间 | 本地同步时间。 |

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

字段中文说明：

| 字段名 | 中文说明 | 备注 |
|---|---|---|
| `customer_id` | 客户/公司 ID | 客户和公司统一后的主键。 |
| `customer_name` | 客户/公司名称 | 主要查询字段。 |
| `customer_type` | 客户性质/类型 | 可由公司字段或接口字段解析。 |
| `province` | 省份 | 地区维度。 |
| `city` | 城市 | 地区维度。 |
| `district` | 区县 | 地区维度。 |
| `address` | 地址 | 客户/公司地址。 |
| `source_flags` | 来源标记 | 例如 `customer,company`，表示来自哪些接口。 |
| `source_updated_at` | 来源更新时间 | 接口返回的更新时间，如有。 |
| `last_sync_at` | 最近同步时间 | 本地最近同步时间。 |
| `created_at` | 入库时间 | 数据库记录创建时间。 |
| `updated_at` | 更新时间 | 数据库记录更新时间。 |

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

字段中文说明：

| 字段名 | 中文说明 | 备注 |
|---|---|---|
| `contact_id` | 联系人 ID | 联系人和公司联系人统一后的主键。 |
| `contact_name` | 联系人姓名 | 主要查询字段。 |
| `phone` | 手机号 | 联系方式。 |
| `email` | 邮箱 | 联系方式。 |
| `qq` | QQ | 联系方式。 |
| `wechat` | 微信 | 联系方式。 |
| `customer_id` | 所属客户/公司 ID | 逻辑关联 `customers.customer_id`。 |
| `customer_name` | 所属客户/公司名称 | 冗余字段，便于查看。 |
| `department_name` | 联系人部门 | 客户内部部门，如接口可返回。 |
| `position_name` | 联系人职位 | 客户联系人职位。 |
| `source_flags` | 来源标记 | 例如 `contact,company_contact`。 |
| `source_updated_at` | 来源更新时间 | 接口返回的更新时间，如有。 |
| `last_sync_at` | 最近同步时间 | 本地最近同步时间。 |
| `created_at` | 入库时间 | 数据库记录创建时间。 |
| `updated_at` | 更新时间 | 数据库记录更新时间。 |

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

字段中文说明：

| 字段名 | 中文说明 | 备注 |
|---|---|---|
| `id` | 日志 ID | 自增主键。 |
| `task_type` | 任务类型 | 例如 `ticket_detail`、`customer`、`contact`。 |
| `target_year` | 目标年份 | 例如 `2025`。 |
| `target_month` | 目标月份 | 例如 `1` 到 `12`。 |
| `target_month_label` | 目标年月 | 格式 `YYYY-MM`。 |
| `status` | 任务状态 | 例如 `running`、`success`、`failed`、`partial`。 |
| `total_count` | 应处理数量 | 本次任务计划处理总量。 |
| `success_count` | 成功数量 | 成功写入或同步数量。 |
| `failed_count` | 失败数量 | 同步失败数量。 |
| `skipped_count` | 跳过数量 | 远端未更新或已存在时跳过数量。 |
| `started_at` | 开始时间 | 任务开始时间。 |
| `finished_at` | 结束时间 | 任务结束时间。 |
| `duration_seconds` | 耗时秒数 | 任务耗时。 |
| `error_message` | 错误摘要 | 任务失败或部分失败时记录。 |
| `extra_json` | 扩展信息 JSON | 可记录失败 ID、分页信息等。 |
| `created_at` | 日志创建时间 | 数据库记录创建时间。 |

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
