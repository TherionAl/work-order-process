# 2025 年月度工单导出说明

更新时间：2026-07-03

## 当前输出目标

项目现在只生成两类数据：

1. `output/2025_monthly_tickets/YYYY-MM_tickets.json`
   - 保存该月通过搜索接口拿到的工单合集。
   - 包含接口声明总量、实际保存数量、工单 ID 列表和列表页返回字段。

2. `output/2025_monthly_sample_details/YYYY-MM_sample_details_*.json`
   - 从该月工单合集里抽 3 条工单详情。
   - 每个月生成三份文件：
     - `raw.json`：详情接口原始值。
     - `value_resolved.json`：英文 key 保留，尽量替换可识别 value。
     - `chinese.json`：在 value 替换后，把 key 中文化。

## 使用的接口

项目配置的 `base_url` 已经是：

```text
https://workorder.bosssoft.com.cn/api/v1
```

代码中实际请求路径不再重复写 `/api/v1`。

| 用途 | 路径 | 说明 |
|---|---|---|
| 按创建月份搜索工单 | `/tickets/search.json?query=createDT:YYYY-MM` | 用于生成每月工单合集。 |
| 获取工单详情 | `/tickets/{ticketId}` | 用于生成每月 3 条样本详情的 raw 文件。 |
| 获取联系人详情 | `/users/{custUserId}` | 用 `user.name` 替换 `custUserId`。 |
| 获取客服详情 | `/supports/{sId}` | 用 `support.name` 替换客服 ID。 |
| 获取客服组详情 | `/supportgroups/{sgId}` | 用 `supportgroup.sgName` 替换客服组 ID。 |
| 获取工单模板详情 | `/tickettemplates/{tId}` | 用 `tickettemplate.ticketTemplateName` 替换模板 ID。 |
| 获取工单所有字段 | `/tickets/ticket_fields2.json` | 解释 `field_xxx` 和字段选项 ID。 |
| 获取公司字段列表 | `/companies/company_fields.json` | 兜底解释工单里引用的公司字段选项，例如“客户性质”。 |

## 详情 value 替换规则

| 字段 | 替换方式 |
|---|---|
| `custUserId` | 调 `/users/{custUserId}`，取联系人姓名。 |
| `servicerUserId`、`createrId`、`deleterId` | 调 `/supports/{sId}`，取客服姓名；空值和 `0` 保留。 |
| `ccUserIdList` | 按逗号拆分客服 ID 后逐个替换姓名。 |
| `servicerGroupId`、`ccGroupIdList` | 调客服组详情接口，取客服组名称。 |
| `ticketTemplateId` | 调工单模板详情接口，取模板名称。 |
| `currentNodeField`、`currentNodeFieldValue` | 用工单字段接口返回的字段名和选项值替换。 |
| `custom_fields` | 把 `field_xxx` 替换为字段中文名，并尽量替换选项 value。 |
| `record_serviceruserid` | 自定义字段中的历史操作人，按客服 ID 替换为客服姓名。 |
| `nodeFieldIntoTime` | 秒级 Unix 时间戳转为可读时间。 |

本地枚举替换：

| 字段 | 映射 |
|---|---|
| `priorityLevel` | `1=低`，`2=正常`，`3=高`，`4=紧急` |
| `ticketStatus` | `1=新建`，`2=已开启`，`3=待回应`，`4=已解决`，`5=已关闭`，`6=已关闭` |
| `ticketType` | `1=问题`，`2=事务`，`3=故障`，`4=任务` |
| `createrType` | `0=客服`，`1=客户` |
| `isDeleted` | `0=否`，`1=是` |

## 运行命令

正式导出：

```powershell
uv run work-order-process run
```

调试小样本：

```powershell
uv run work-order-process run --limit-per-month 10 --overwrite
```

单月续跑：

```powershell
uv run work-order-process run --month 3
```

抽样默认固定随机种子为 `2025`。如果需要更换每月 3 条样本，可传入新的 `--seed`。
搜索接口分页默认使用 `per_page=5000`；当前已验证 `10000` 会返回 500，不作为默认值。
