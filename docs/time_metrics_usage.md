# 工单节点工作时长指标说明

本功能用于从工单 `custom_fields` 中读取指定的两个时间节点，计算两点之间的工作时间分钟数，并输出 JSON 供业务验证。

当前阶段不新增数据库表、不回写主表，只导出验证文件。

## 1. 工作时间规则

默认工作时间段在 `config/work_calendar_cn_2026.json` 中配置：

```text
09:00-11:50
13:40-18:00
```

计算时会扣除：

- 周末
- 中国法定节假日
- 调休休息日
- 工作日的非工作时间

默认规则是周一至周五为工作日，周六日为休息日；JSON 中的 `days` 用于覆盖节假日和调休上班日。

## 2. 指标配置

指标配置文件：

```text
config/time_metrics.json
```

示例：

```json
{
  "metrics": [
    {
      "code": "example_node_duration",
      "name": "示例节点工作时长",
      "start_field": "field_249",
      "end_field": "field_331",
      "unit": "minutes",
      "enabled": true
    }
  ]
}
```

字段含义：

```text
code         指标唯一编码，建议使用英文小写和下划线
name         中文名称，输出 JSON 中展示
start_field  开始节点字段 key，例如 field_249
end_field    结束节点字段 key，例如 field_331
unit         当前固定 minutes
enabled      是否启用
```

## 3. 新增一个节点时长指标

只需要在 `metrics` 数组中增加一项。例如新增“审批时长”：

```json
{
  "code": "approval_duration",
  "name": "审批时长",
  "start_field": "field_249",
  "end_field": "field_331",
  "unit": "minutes",
  "enabled": true
}
```

新增“处理时长”：

```json
{
  "code": "handle_duration",
  "name": "处理时长",
  "start_field": "field_100",
  "end_field": "field_200",
  "unit": "minutes",
  "enabled": true
}
```

后续如果同一个工单要统计多个节点组合，就继续增加多条配置。核心计算代码不用改。

## 4. 输出 JSON

按月导出：

```powershell
uv run work_order_process_v1.1 metric-month --year 2026 --month 6
```

只导出某一个指标：

```powershell
uv run work_order_process_v1.1 metric-month --year 2026 --month 6 --metric-code approval_duration
```

只导出前 100 条用于验证：

```powershell
uv run work_order_process_v1.1 metric-month --year 2026 --month 6 --limit-per-month 100
```

按单个工单导出：

```powershell
uv run work_order_process_v1.1 metric-ticket --ticket-id 22256891
```

默认输出目录：

```text
output/time_metrics/
```

输出字段：

```text
ticket_id
create_dt
create_month_label
ticket_template_id
subject
metric_code
metric_name
start_field
end_field
start_time
end_time
raw_minutes
business_minutes
status
error_message
```

## 5. 状态说明

```text
success              正常计算
missing_start        开始节点为空
missing_end          结束节点为空
missing_both         两个节点都为空
invalid_start        开始节点无法解析为时间
invalid_end          结束节点无法解析为时间
invalid_time_order   结束时间早于开始时间
```

缺失或异常时，`business_minutes` 留空。

## 6. 后续入库方案

业务指标稳定后，再考虑新增指标长表，例如：

```text
ticket_time_metrics
```

建议字段：

```text
ticket_id
create_dt
create_month_label
metric_code
metric_name
start_field
end_field
start_time
end_time
raw_minutes
business_minutes
calendar_code
work_time_rule
status
error_message
calculated_at
source_updated_at
```

当前阶段先不建表，避免在指标口径未稳定前固化结构。
