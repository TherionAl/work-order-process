# MySQL 入库设计

更新时间：2026-07-03

## 目标

项目在本机 MySQL 中只维护两张工单详情表：

1. `ticket_detail_main`
   - 一条工单一行。
   - 保存工单详情顶层字段，排除 `custom_fields`。

2. `ticket_detail_custom_fields`
   - 一条自定义字段一行。
   - 保存 `custom_fields` 动态字段，避免把不同模板的字段强行摊成超宽表。

不建 `ticket_detail_raw`，也不建附件表。数组和对象字段使用 MySQL `JSON` 字段或 JSON 字符串保存。

## 本机配置

把 MySQL 连接信息写入本地 `.env`：

```dotenv
WORKORDER_MYSQL_HOST=127.0.0.1
WORKORDER_MYSQL_PORT=3306
WORKORDER_MYSQL_USER=root
WORKORDER_MYSQL_PASSWORD=你的本机MySQL密码
WORKORDER_MYSQL_DATABASE=work_order
```

## 命令

初始化数据库和表：

```powershell
uv run work-order-process mysql-init
```

拉取单条工单详情并写入 MySQL：

```powershell
uv run work-order-process mysql-import-ticket --ticket-id 22256891
```

## 表结构摘要

### ticket_detail_main

主键：

- `ticket_id`

主要索引：

- `ticket_template_id`
- `ticket_status`
- `create_dt`
- `update_dt`
- `servicer_user_id`
- `servicer_group_id`

字段来源：

- 使用 value 替换后的工单详情顶层字段。
- `custom_fields` 不进主表。
- `descriptattachments` 保存为 JSON。

### ticket_detail_custom_fields

主键：

- `id`

唯一约束：

- `(ticket_id, field_order)`

主要索引：

- `ticket_id`
- `(ticket_template_id, field_name)`
- `field_name`
- `field_key`

字段来源：

- `field_key`：原始字段 key，例如 `field_1212`。
- `field_name`：转换后的中文字段名，例如 `吉林一体化流程节点`。
- `field_value`：文本值；数组或对象会转成 JSON 字符串。
- `field_value_json`：数组或对象原结构对应的 JSON。
- `field_value_type`：`str`、`list`、`dict`、`null` 等。

## 更新策略

单条工单写入时：

1. `ticket_detail_main` 使用 `INSERT ... ON DUPLICATE KEY UPDATE`。
2. `ticket_detail_custom_fields` 先按 `ticket_id` 删除旧字段，再整批插入当前详情里的全部 `custom_fields`。

这样可以避免字段变更后留下旧的动态字段。

## 已验证

已用 `22256891` 验证：

- `ticket_detail_main` 写入 1 行。
- `ticket_detail_custom_fields` 写入 189 行。
