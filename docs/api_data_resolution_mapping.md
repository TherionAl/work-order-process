# 工单详情字段解释与接口映射说明

更新时间：2026-07-03

本文档记录当前项目对工单详情 JSON 的三段式处理规则，以及每类字段值对应调用的接口来源。

## 输出文件

项目会生成三份工单详情文件：

- `output/ticket_details_raw.json`：工单详情接口原始返回值，英文 key 和原始 value 都保留。
- `output/ticket_details_value_resolved.json`：英文 key 保留，尽量把 ID、枚举、字段选项 value 替换为可读中文。
- `output/ticket_details_chinese.json`：在 value 已替换的基础上，再用数据字典和项目兜底字典把 key 中文化。

按月导出时会额外生成：

- `output/monthly_ticket_ids/YYYY-MM_ticket_ids.json`：某月工单 ID 列表，来自创建时间搜索接口。
- `output/monthly_ticket_details/YYYY-MM_ticket_details_raw.json`：某月工单详情原始值。
- `output/monthly_ticket_details/YYYY-MM_ticket_details_value_resolved.json`：某月工单详情 value 替换结果。
- `output/monthly_ticket_details/YYYY-MM_ticket_details_chinese.json`：某月工单详情中文字段结果。

## 认证方式

所有业务接口使用 HTTP Basic Auth：

```python
from requests.auth import HTTPBasicAuth

response = requests.post(url, data=data, auth=HTTPBasicAuth(username, password))
```

项目中通过 `httpx.BasicAuth` 实现同等认证方式。账号、密码和地址前缀从 `.env` 或 `agents.md` 读取。

## 基础接口

| 用途 | 文档地址 | 实际调用路径 | 说明 |
|---|---|---|---|
| 获取工单列表 | https://doc.bangwo8.com/438724239e0 | `/tickets` | 先按 2025 年后工单抽样，得到工单号。 |
| 按创建时间搜索工单 | 通用搜索接口 | `/tickets/search.json?query=createDT:YYYY-MM` | 当前已验证可按月份返回匹配数量和列表页结果，用于月度 ID 导出。 |
| 获取工单详情 | https://doc.bangwo8.com/438724240e0 | `/tickets/{ticketId}` | 生成 `ticket_details_raw.json` 的原始数据来源。 |
| 获取工单自定义字段 | https://doc.bangwo8.com/438724249e0 | `/tickets/ticket_fields.json` | 返回工单自定义字段定义。 |
| 获取工单所有字段 | https://doc.bangwo8.com/438724250e0 | `/tickets/ticket_fields2.json` | 当前用于解释 `field_xxx` 和字段选项 ID。 |
| 获取公司字段列表 | https://doc.bangwo8.com/438724233e0 | `/companies/company_fields.json` | 当前用于补充工单里引用公司字段选项的值，例如“客户性质”。 |

说明：通用文档展示的完整路径是 `/api/v1/...`。项目配置的 `base_url` 已经是 `https://workorder.bosssoft.com.cn/api/v1`，所以代码里实际请求路径不再重复写 `/api/v1`。

## 顶层字段 value 替换规则

| 工单详情字段 | 原始值示例 | 替换后示例 | 接口或规则 |
|---|---:|---|---|
| `custUserId` | `39916305` | `开票员` | `/users/{custUserId}`，取 `user.name`。 |
| `servicerUserId` | `1265487` | `程玺燃` | `/supports/{sId}`，取 `support.name`。 |
| `createrId` | `1265487` | `程玺燃` | `/supports/{sId}`，取 `support.name`。 |
| `deleterId` | `0` | 保留 `0` | `0` 表示无删除人，不调用接口。 |
| `ccUserIdList` | `1265487,1206855` | `程玺燃,杨培超` | 按逗号拆分后逐个调用 `/supports/{sId}`。 |
| `servicerGroupId` | `332407` | `【新疆】南区（自治区）` | `/supportgroups/{sgId}`，取 `supportgroup.sgName`。 |
| `ccGroupIdList` | `329301,329322` | 客服组名称列表 | 按逗号拆分后逐个调用 `/supportgroups/{sgId}`。 |
| `ticketTemplateId` | `4` | `服务请求单` | `/tickettemplates/{tId}`，取 `tickettemplate.ticketTemplateName`。 |
| `currentNodeField` | `field_31` | `服务流程节点状态` | `/tickets/ticket_fields2.json`，按 `key -> name` 映射。 |
| `currentNodeFieldValue` | `4273140` | `线上服务完成` | `/tickets/ticket_fields2.json`，按字段的 `custom_field_options` 映射。 |
| `nodeFieldIntoTime` | `1769757489` | `2026-01-30 15:18:09` | 秒级 Unix 时间戳转本地可读时间。 |

## 枚举字段 value 替换规则

这些枚举值来自接口文档和实际返回值的含义整理，直接在项目内维护：

| 字段 | 映射 |
|---|---|
| `priorityLevel` | `1=低`，`2=正常`，`3=高`，`4=紧急` |
| `ticketStatus` | `1=新建`，`2=已开启`，`3=待回应`，`4=已解决`，`5=已关闭`，`6=已关闭` |
| `ticketType` | `1=问题`，`2=事务`，`3=故障`，`4=任务` |
| `createrType` | `0=客服`，`1=客户` |
| `isDeleted` | `0=否`，`1=是` |

注意：当前样本里 `ticketType` 出现过 `0`，通用文档未给出明确含义，所以保留原值。

## 自定义字段解析规则

工单详情中的 `custom_fields` 结构通常如下：

```json
{"key": "field_12", "value": "4272477"}
```

处理后变为：

```json
{"key": "工单接入渠道", "value": "其他"}
```

当前自定义字段解析流程：

1. 调用 `/tickets/ticket_fields2.json` 获取工单字段定义。
2. 用字段定义中的 `key` 把 `field_xxx` 替换为 `name`。
3. 如果字段有 `custom_field_options`，用选项 `key -> value` 替换字段值。
4. 如果字段值是列表，则逐项替换。
5. 如果字段选项是级联或嵌套结构，则递归收集选项。
6. 如果工单字段选项没有命中，再用公司字段选项 `/companies/company_fields.json` 作为兜底。

## 月度导出流程

当前保留的月度查询方法是：

```text
GET /tickets/search.json?query=createDT:2025-01&sort_by=createDT&sort_order=asc&page=1&per_page=1000
```

流程分两步：

1. `ticket-month-ids`：按 `YYYY-MM` 分页获取工单列表，保存 `ticket_ids`。
2. `ticket-month-details`：读取该月 `ticket_ids`，逐条调用 `/tickets/{ticketId}` 获取详情，随后复用现有 value 替换和 key 中文化逻辑。

如果用 `--limit` 生成了部分 ID 文件，后续不带 `--limit` 跑整月详情时会被拦截，需要先用 `--overwrite` 重新生成完整月度 ID，避免把样本误当成全量数据。

已统计 2025 年各月数量如下：

| 月份 | 工单数 |
|---|---:|
| 2025-01 | 74040 |
| 2025-02 | 69553 |
| 2025-03 | 92483 |
| 2025-04 | 83785 |
| 2025-05 | 58536 |
| 2025-06 | 58767 |
| 2025-07 | 55212 |
| 2025-08 | 49835 |
| 2025-09 | 62167 |
| 2025-10 | 48419 |
| 2025-11 | 54148 |
| 2025-12 | 62792 |
| 合计 | 769737 |

## 已确认的特殊字段

| 中文字段 | 原始 key | 原始 value | 替换后 | 来源 |
|---|---|---:|---|---|
| 服务流程节点状态 | `field_31` | `4273140` | `线上服务完成` | 工单字段接口 `/tickets/ticket_fields2.json` |
| 运维事件流程节点 | `field_609` | `4320484` | `线上运维完成` | 工单字段接口 `/tickets/ticket_fields2.json` |
| 任务执行流程节点状态 | `field_222` | `4309140` | `工单完结` | 工单字段接口 `/tickets/ticket_fields2.json` |
| 历史操作人 | `record_serviceruserid` | `1262670` | `向飞龙` | 值是客服 ID，调用 `/supports/{sId}` |
| 历史操作人 | `record_serviceruserid` | `1206855` | `杨培超` | 值是客服 ID，调用 `/supports/{sId}` |
| 客户性质 | `field_1391` | `4318078` | `其他行业` | 公司字段接口 `/companies/company_fields.json` |
| 客户性质 | `field_1391` | `4318081` | `在线客户` | 公司字段接口 `/companies/company_fields.json` |

## key 中文化来源

顶层 key 中文化主要来自 `数据字典-帮我吧.pdf` 中的 `tickets` 表字段定义。

如果 PDF 中没有对应字段，项目使用 `src/work_order_process/dictionary.py` 中的 `EXTRA_FIELD_LABELS` 兜底，例如：

| 英文字段 | 中文字段 |
|---|---|
| `custom_fields` | 自定义字段 |
| `currentNodeField` | 当前流程节点字段 |
| `currentNodeFieldValue` | 当前流程节点值 |
| `nodeFieldIntoTime` | 进入节点时间 |
| `servicerGroupId` | 客服组 |
| `ccGroupIdList` | 抄送客服组 |

## 当前仍保留原值的字段

| 字段 | 原因 |
|---|---|
| `agentId` | `/agent/agent_info.json` 当前账号返回“不是主账号”，无法取得服务商/代理商解释。 |
| `ticketSource` | 通用文档和当前可用接口未返回明确来源枚举；`/ticketsources` 实际返回工单列表，不是来源字典。 |
| `customTemplateId` | 未找到能把 `2` 映射为名称的可用接口；模板接口解释的是 `ticketTemplateId`。 |
| `workflow_id`、`workflow_node_id` | 当前样本为 `0` 或空，文档未提供可用映射。 |
| `ticketId`、`subject`、`createDT`、`updateDT`、`waitDT`、`solveDT` | 这些本身就是业务值或时间，不需要替换。 |

## 相关代码位置

| 文件 | 作用 |
|---|---|
| `src/work_order_process/api.py` | 封装所有接口请求、Basic Auth、详情接口和字段字典接口。 |
| `src/work_order_process/resolver.py` | 实现工单 value 替换逻辑，包括自定义字段和特殊字段。 |
| `src/work_order_process/dictionary.py` | 解析 PDF 数据字典，并把 key 中文化。 |
| `src/work_order_process/cli.py` | 命令行入口，例如 `ticket-details-refresh` 和 `ticket-details-resolved`。 |
