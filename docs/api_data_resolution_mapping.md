# API 数据解析和字段映射说明

更新时间：2026-07-07

本文档说明组合版项目中工单 API、字段解析、value 处理、JSON 导出和 MySQL 入库之间的对应关系。

数据库最终结构、分区、同步策略详见 `docs/mysql_schema.md`；整体开发进度详见 `docs/development_progress.md`。

## 当前输出目标

项目现在输出三类数据：

1. 月度工单列表：

```text
output/YYYY_monthly_tickets/YYYY-MM_tickets.json
```

2. 月度样本详情：

```text
output/YYYY_monthly_sample_details/YYYY-MM_sample_details_raw.json
output/YYYY_monthly_sample_details/YYYY-MM_sample_details_value_resolved.json
output/YYYY_monthly_sample_details/YYYY-MM_sample_details_chinese.json
```

3. MySQL 数据湖：

```text
ticket_detail_main
ticket_detail_custom_fields
customers
contacts
sync_task_log
```

## 使用的 API

项目配置中的 `base_url` 已经包含：

```text
https://workorder.bosssoft.com.cn/api/v1
```

代码中实际请求路径不再重复写 `/api/v1`。

| 用途 | 路径 | 说明 |
|---|---|---|
| 按创建月份搜索工单 | `/tickets/search.json?query=createDT:YYYY-MM` | 生成月度工单合集，按页获取。 |
| 按创建月份和模板搜索工单 | `/tickets/search.json?query=createDT:YYYY-MM ticketTemplateId:<id>` | 按模板抽样。 |
| 获取工单详情 | `/tickets/{ticketId}` | 生成 raw 详情和入库数据。 |
| 获取联系人详情 | `/users/{custUserId}` | 补充 `cust_user_name`。 |
| 获取客服详情 | `/supports/{sId}` | 补充 `servicer_user_name`、`creater_name`、`deleter_name`。 |
| 获取客服组详情 | `/supportgroups/{sgId}` | 补充 `servicer_group_name`。 |
| 获取工单模板详情 | `/tickettemplates/{tId}` | 补充 `ticket_template_name`。 |
| 获取工单模板列表 | `/tickettemplates` | 按模板抽样时使用。 |
| 获取工单字段定义 | `/tickets/ticket_fields2.json` | 解释 `field_xxx` 和字段选项 ID。 |
| 获取公司字段定义 | `/companies/company_fields.json` | 补充解释工单中引用的公司字段选项。 |

## 三段式详情文件

### raw

`raw` 保存工单详情接口原始返回值，不修改字段名和值。

用途：

- 对照源系统数据。
- 排查 resolver 解析问题。
- 作为后续重新生成 `value_resolved` 和 `chinese` 的基础。

### value_resolved

`value_resolved` 保留英文 key，但会解析枚举、人员名称、模板名称和自定义字段选项。

组合版的重要语义：

- 原始 ID 字段不覆盖。
- 新增 `*_name` 字段保存可读名称。
- 自定义字段 `custom_fields[].key` 会尽量从 `field_xxx` 转成字段中文名。
- 自定义字段 `custom_fields[].value` 会尽量把选项 ID 转成可读值。
- 高频分析维度会从自定义字段提取到顶层字段。

示例：

```json
{
  "custUserId": "100",
  "cust_user_name": "张三",
  "servicerUserId": "200",
  "servicer_user_name": "李四",
  "ticketTemplateId": "400",
  "ticket_template_name": "产品咨询模板"
}
```

### chinese

`chinese` 基于 `value_resolved` 生成：

- value 已经按规则解析。
- key 再通过数据字典 PDF 转成中文字段名。

该文件主要用于人工查看，不建议作为入库主数据源。

## 顶层字段解析规则

| 原始字段 | 处理方式 | 入库字段 |
|---|---|---|
| `ticketId` | 转为整数 | `ticket_id` |
| `subject` | 原样保存 | `subject` |
| `descript` | 原样保存 | `descript` |
| `custUserId` | 保留 ID，并查联系人详情补名称 | `cust_user_id`、`cust_user_name` |
| `servicerUserId` | 保留 ID，并查客服详情补名称 | `servicer_user_id`、`servicer_user_name` |
| `createrId` | 保留 ID，并查客服详情补名称 | `creater_id`、`creater_name` |
| `deleterId` | 保留 ID，并查客服详情补名称 | `deleter_id`、`deleter_name` |
| `ccUserIdList` | 拆分后逐个解析，保存为文本 | `cc_user_id_list` |
| `servicerGroupId` | 保留 ID，并查客服组详情补名称 | `servicer_group_id`、`servicer_group_name` |
| `ccGroupIdList` | 拆分后逐个解析，保存为文本 | `cc_group_id_list` |
| `ticketTemplateId` | 保留 ID，并查模板详情补名称 | `ticket_template_id`、`ticket_template_name` |
| `ticketType` | 本地枚举解析 | `ticket_type` |
| `priorityLevel` | 本地枚举解析 | `priority_level` |
| `ticketStatus` | 本地枚举解析 | `ticket_status` |
| `createrType` | 本地枚举解析 | `creater_type` |
| `isDeleted` | 本地枚举解析 | `is_deleted` |
| `createDT` | 转为 datetime | `create_dt` |
| `updateDT` | 转为 datetime，用于增量判断 | `source_updated_at` |
| `solveDT` | 转为 datetime | `solve_dt` |
| `waitDT` | 转为 datetime | `wait_dt` |
| `openDT` | 转为 datetime | `open_dt` |
| `closeDT` | 转为 datetime | `close_dt` |
| `nodeFieldIntoTime` | 秒级 Unix 时间戳转 datetime | `node_field_into_time` |
| `descriptattachments` | 对象/数组保存为 JSON | `descript_attachments` |

## 本地枚举映射

| 字段 | 映射 |
|---|---|
| `ticketType` | `1=问题`、`2=事务`、`3=故障`、`4=任务` |
| `priorityLevel` | `1=低`、`2=正常`、`3=高`、`4=紧急` |
| `ticketStatus` | `1=新建`、`2=已开启`、`3=待回应`、`4=已解决`、`5=已关闭`、`6=已关闭` |
| `createrType` | `0=客服`、`1=客户` |
| `isDeleted` | `0=否`、`1=是` |

## 自定义字段解析

原始结构通常类似：

```json
{
  "custom_fields": [
    {
      "key": "field_1212",
      "value": "1"
    }
  ]
}
```

解析流程：

1. 读取工单字段定义 `/tickets/ticket_fields2.json`。
2. 建立 `field_key -> field_name` 的映射。
3. 建立字段选项 `option_key -> option_value` 的映射。
4. 处理 `custom_fields`：
   - `raw.custom_fields[].key` 作为入库 `field_key`。
   - `value_resolved.custom_fields[].key` 作为入库 `field_name`。
   - `value_resolved.custom_fields[].value` 作为入库 `field_value` 或 `field_value_json`。

特殊规则：

- `record_serviceruserid*` 类型字段会按客服 ID 解析为客服姓名。
- 数组、对象类型保留 JSON 结构。
- 普通字符串、数字、布尔值保存为文本。

## 高频分析维度提取

组合版会从解析后的 `custom_fields` 中按字段名关键字提取常用分析维度，并写入 `ticket_detail_main` 顶层字段。

当前维度包括：

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
department_name
current_node_name
current_node_status
current_node_started_at
```

提取后可以直接做报表查询，避免每次从自定义字段明细表中聚合。

## MySQL 入库映射

### ticket_detail_main

顶层字段进入 `ticket_detail_main`：

```text
ticketId              -> ticket_id
subject               -> subject
descript              -> descript
custUserId            -> cust_user_id
cust_user_name        -> cust_user_name
servicerUserId        -> servicer_user_id
servicer_user_name    -> servicer_user_name
ticketTemplateId      -> ticket_template_id
ticket_template_name  -> ticket_template_name
createDT              -> create_dt
updateDT              -> source_updated_at
```

同时派生：

```text
create_year
create_month
create_month_label
last_sync_at
sync_status
sync_error
```

### ticket_detail_custom_fields

`custom_fields` 进入 `ticket_detail_custom_fields`：

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

## 月度导出命令

导出月度工单合集和每月样本详情：

```powershell
uv run work_order_process run --year 2026
```

只导出月度工单合集，不拉详情样本：

```powershell
uv run work_order_process monthly-tickets --year 2026
```

限制调试数据量：

```powershell
uv run work_order_process run --year 2026 --limit-per-month 10 --overwrite
```

控制样本详情并发：

```powershell
uv run work_order_process run --year 2026 --detail-workers 4
```

## 按模板抽样

```powershell
uv run work_order_process template-samples --year 2026 --month 6 --sample-size 3 --seed 202606 --overwrite
```

流程：

1. 调 `/tickettemplates` 获取模板列表。
2. 对每个模板执行：

```text
GET /tickets/search.json?query=createDT:YYYY-MM ticketTemplateId:<模板ID>
```

3. 每个有数据的模板抽取指定数量工单。
4. 输出三段式详情 JSON。

## MySQL 导入命令

初始化：

```powershell
uv run work_order_process mysql-init
```

单条工单：

```powershell
uv run work_order_process mysql-import-ticket --ticket-id 22256891
```

单月：

```powershell
uv run work_order_process mysql-import-month --year 2025 --month 1
```

全年：

```powershell
uv run work_order_process mysql-import-year --year 2025
```

客户和联系人：

```powershell
uv run work_order_process mysql-import-customers
uv run work_order_process mysql-import-contacts
```

同步日志：

```powershell
uv run work_order_process mysql-sync-log --log-limit 20
```

## 当前语义和旧根目录版差异

旧根目录版文档中描述过“把 ID 字段直接替换成中文名称”的方式。组合版已经调整：

| 项目 | 旧语义 | 当前组合版语义 |
|---|---|---|
| 联系人 | `custUserId` 可能直接替换成姓名 | `custUserId` 保留 ID，新增 `cust_user_name` |
| 客服 | `servicerUserId` 可能直接替换成姓名 | `servicerUserId` 保留 ID，新增 `servicer_user_name` |
| 客服组 | `servicerGroupId` 可能直接替换成组名 | `servicerGroupId` 保留 ID，新增 `servicer_group_name` |
| 模板 | `ticketTemplateId` 可能直接替换成模板名 | `ticketTemplateId` 保留 ID，新增 `ticket_template_name` |
| 自定义字段 | 主要用于 JSON 查看 | 同时服务 JSON 查看和 MySQL 明细入库 |

这样做更适合长期数据仓库：既能追溯源系统主键，也能直接展示可读名称。
