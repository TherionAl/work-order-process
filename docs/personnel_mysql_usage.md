# 人员名单 MySQL 导入说明

## 表结构

`mysql-import-personnel` 会把根目录的 `人员信息名单20260708.xls` 导入到 `personnel` 表。

| Excel 字段 | MySQL 字段 | 说明 |
| --- | --- | --- |
| 人员姓名 | `person_name` | 人员姓名 |
| 工号 | `employee_no` | 主键，导入时转成字符串，避免 Excel 数字工号出现 `.0` |
| 所属省份 | `province` | 所属省份或区域 |
| 角色 | `role_names` | 原始角色文本 |
| 所属组 | `group_name` | 所属组 |

## 导入命令

```powershell
uv run work_order_process_v1.1 mysql-import-personnel
```

指定其他文件：

```powershell
uv run work_order_process_v1.1 mysql-import-personnel --personnel-file .\人员信息名单20260708.xls
```

## 关联查询

当前工单主表中的 `servicer_user_id`、`creater_id` 是帮我吧内部用户 ID，和人员名单中的工号不是同一个编号体系。已经入库的数据里，按工号直接关联没有命中；按姓名可以命中多数工单，但存在少量重名，需要业务上确认是否可接受。

按客服姓名关联：

```sql
SELECT
  t.ticket_id,
  t.subject,
  t.servicer_user_id,
  t.servicer_user_name,
  p.employee_no,
  p.province,
  p.role_names,
  p.group_name
FROM ticket_detail_main AS t
JOIN personnel AS p
  ON t.servicer_user_name = p.person_name
WHERE t.create_month_label = '2026-06';
```

按创建人姓名关联：

```sql
SELECT
  t.ticket_id,
  t.subject,
  t.creater_id,
  t.creater_name,
  p.employee_no,
  p.province,
  p.role_names,
  p.group_name
FROM ticket_detail_main AS t
JOIN personnel AS p
  ON t.creater_name = p.person_name
WHERE t.create_month_label = '2026-06';
```

如果后续能获取帮我吧内部用户 ID 和工号的映射，建议新增映射列或映射表，再改成 ID 级关联。
