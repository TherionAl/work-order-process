# 工单数据处理项目开发进度总结

更新时间：2026-07-06

## 项目目标

本项目用于从帮我吧工单系统接口获取工单、客户、联系人等数据，并完成：

1. 工单按月份获取和本地备份。
2. 工单详情字段值解析和中文化。
3. 工单详情结构化。
4. 后续写入本机 MySQL，形成可查询、可分析的数据仓库。

当前接口认证方式为 HTTP Basic Auth，账号、密码和接口地址优先从 `.env` 或 `agents.md` 读取。

## 当前已实现功能

### 1. 2025 年工单按月获取

已确认工单搜索接口支持按创建月份查询：

```text
GET /tickets/search.json?query=createDT:YYYY-MM
```

项目已实现：

```powershell
uv run work-order-process run
```

输出目录：

```text
output/2025_monthly_tickets/
```

已成功获取 2025 年 1-12 月工单列表合集，合计：

```text
769737 条
```

### 2. 每月工单详情抽样

项目会从 2025 年每个月工单中随机抽 3 条详情，并生成三份 JSON：

```text
output/2025_monthly_sample_details/
```

每个月输出：

```text
YYYY-MM_sample_details_raw.json
YYYY-MM_sample_details_value_resolved.json
YYYY-MM_sample_details_chinese.json
```

三份文件含义：

- `raw`：接口原始返回。
- `value_resolved`：英文 key 保留，尽量把 ID、枚举、自定义字段选项替换为可读中文。
- `chinese`：在 value 替换后，再用数据字典把 key 中文化。

### 3. 2026 年 6 月按模板抽样

已确认搜索接口支持按月份和模板组合查询：

```text
GET /tickets/search.json?query=createDT:2026-06 ticketTemplateId:<模板ID>
```

项目已实现命令：

```powershell
uv run work-order-process template-samples --year 2026 --month 6 --sample-size 3 --seed 202606 --overwrite
```

输出目录：

```text
output/2026_06_template_sample_details/
```

已识别 2026 年 6 月有数据的 9 个模板，每个模板抽 3 条，共 27 条详情。

### 4. 单条工单详情 Excel 结构化导出

已实现脚本：

```powershell
.\.venv\Scripts\python.exe scripts\export_ticket_detail_excel.py 22256891 --output output\ticket_22256891_structured.xlsx
```

Excel 内包含两个 sheet：

- `ticket_detail_main`
- `ticket_detail_custom_fields`

已用工单 `22256891` 验证：

- 主表 sheet：35 行，含表头。
- 自定义字段 sheet：190 行，含表头。

### 5. MySQL 单条工单入库

已引入依赖：

```text
pymysql
```

已实现模块：

```text
src/work_order_process/mysql_storage.py
```

已实现命令：

```powershell
uv run work-order-process mysql-init
uv run work-order-process mysql-import-ticket --ticket-id 22256891
```

已在本机 MySQL 验证：

- 数据库：`work_order`
- `ticket_detail_main` 写入 1 行。
- `ticket_detail_custom_fields` 写入 189 行。

当前代码中的 MySQL 表结构还是第一版基础结构，后续需要按最新设计升级为分区版和 5 表模型。

## 当前已确认的数据仓库设计

最终建议在同一个 MySQL 数据库中维护 5 张表：

```text
work_order
├── ticket_detail_main
├── ticket_detail_custom_fields
├── customers
├── contacts
└── sync_task_log
```

### 1. ticket_detail_main

定位：

- 工单唯一主表。
- 一条工单一行。
- 保存工单详情顶层字段。
- 同时冗余高频分析维度字段。

建议特性：

- 数据量大。
- 按 `create_dt` 月度分区。
- 2025 年历史数据按月份导入。

建议补充字段：

```text
create_year
create_month
create_month_label
source_updated_at
last_sync_at
sync_status
sync_error
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

说明：

- `create_dt` 用于分区。
- `create_month_label` 用于按月查询。
- `source_updated_at` 来自接口 `updateDT`，用于判断远端工单是否更新。
- `last_sync_at` 是本地最近同步时间。

### 2. ticket_detail_custom_fields

定位：

- 工单自定义字段明细表。
- 一条 `custom_fields` 字段一行。
- 用多行承接动态字段，避免形成超宽表。

建议特性：

- 数据量最大。
- 也建议按 `create_dt` 月度分区。
- 分区后不建议使用 MySQL 外键，改由程序逻辑保证关联。
- 冗余年月字段，方便直接按月份查自定义字段。

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

说明：

- `field_key` 保存原始字段 key，例如 `field_1212`。
- `field_name` 保存转换后的中文字段名。
- 普通值保存到 `field_value`。
- 数组或对象保存到 `field_value_json`，同时也可转文本放入 `field_value`。

### 3. customers

定位：

- 客户/公司统一表。
- 接口里的“客户”和“公司”本质上是同一类实体。

命名统一：

```text
客户 = 公司 = customers
```

建议特性：

- 普通表即可，暂不分区。
- 用 `customer_id` 去重合并。
- 用 `source_flags` 记录来源，例如 `customer,company`。

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
```

### 4. contacts

定位：

- 联系人/公司联系人统一表。
- 接口里的“联系人”和“公司联系人”本质上是同一类实体。

命名统一：

```text
联系人 = 公司联系人 = contacts
```

建议特性：

- 普通表即可，暂不分区。
- 用 `contact_id` 去重合并。
- 用 `customer_id` 关联 `customers`。
- 用 `source_flags` 记录来源，例如 `contact,company_contact`。

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
```

### 5. sync_task_log

定位：

- 同步任务日志表。
- 不是业务数据表。
- 用于记录每次批量同步或导入任务的执行情况。

建议记录：

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
```

用途：

- 判断某个月是否跑过。
- 判断任务是否中断。
- 记录成功、失败、跳过数量。
- 记录失败 ID 或错误摘要。

## 分区设计结论

已确认：

1. `ticket_detail_main` 需要按月分区。
2. `ticket_detail_custom_fields` 数据量更大，也建议按月分区。
3. 两张表建议使用同一个分区字段：`create_dt`。
4. 分区表不建议使用 MySQL 外键。
5. 分区表的主键和唯一键必须包含分区字段。

建议分区方式：

```sql
PARTITION BY RANGE COLUMNS(create_dt) (
  PARTITION p202501 VALUES LESS THAN ('2025-02-01'),
  PARTITION p202502 VALUES LESS THAN ('2025-03-01'),
  ...
  PARTITION p202512 VALUES LESS THAN ('2026-01-01'),
  PARTITION pmax VALUES LESS THAN (MAXVALUE)
);
```

## 工单更新策略

每次同步工单详情时：

1. 拉取接口详情。
2. 读取远端 `updateDT`。
3. 对比本地 `source_updated_at`。
4. 如果远端 `updateDT` 没有变化：
   - 可以跳过主数据更新。
   - 只更新 `last_sync_at` 或记录 skipped。
5. 如果远端 `updateDT` 有变化：
   - upsert `ticket_detail_main`。
   - 删除该 `ticket_id` 的旧 `ticket_detail_custom_fields`。
   - 插入当前详情里的全部 `custom_fields`。

自定义字段表采用整条重刷策略，避免字段新增、删除、顺序变化时留下旧数据。

## 已验证命令

```powershell
uv sync
uv run pytest
uv run work-order-process run
uv run work-order-process run --month 3
uv run work-order-process template-samples --year 2026 --month 6 --sample-size 3 --seed 202606 --overwrite
uv run work-order-process mysql-init
uv run work-order-process mysql-import-ticket --ticket-id 22256891
```

## 后续开发计划

### 第一阶段：升级 MySQL 表结构

需要把当前 `mysql_storage.py` 里的基础 DDL 升级为最新 5 表模型：

- `ticket_detail_main` 分区版。
- `ticket_detail_custom_fields` 分区版。
- 新增 `customers`。
- 新增 `contacts`。
- 新增 `sync_task_log`。

同时补充：

- 年月字段。
- 同步状态字段。
- 高频分析维度字段。

### 第二阶段：实现 2025 工单按月入库

基于已有：

```text
output/2025_monthly_tickets/YYYY-MM_tickets.json
```

逐月读取工单 ID，再调用详情接口，写入：

- `ticket_detail_main`
- `ticket_detail_custom_fields`
- `sync_task_log`

### 第三阶段：客户和联系人入库

把以下接口结果统一入库：

- 客户接口 -> `customers`
- 公司接口 -> `customers`
- 联系人接口 -> `contacts`
- 公司联系人接口 -> `contacts`

使用 `source_flags` 记录实体出现过的接口来源。

### 第四阶段：分析维度增强

根据实际分析需求，从 `custom_fields` 中抽取高频字段到 `ticket_detail_main`：

- 地区
- 产品线
- 模块
- 问题类型
- 客户性质
- 当前节点
- 当前节点耗时
- 内部部门

后续如果需要更精细的节点流转分析，再新增：

```text
ticket_node_history
dim_region
dim_department
support_department_map
```

这些属于中期扩展，不建议第一阶段一次性做重。
