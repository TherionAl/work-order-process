# 工单数据获取及解析处理

本项目用于从帮我吧工单接口获取客户、联系人和工单数据，并根据 `数据字典-帮我吧.pdf` 将接口字段转换为数据字典中的中文含义。

## 环境

```powershell
uv sync
```

配置优先级：

1. `.env`
2. `agents.md` 中的 `USERNAME`、`PASSWORD`、实际项目地址前缀

接口认证使用 HTTP Basic Auth。列表接口当前可用方式是 `GET`，认证方式等价于：

```python
requests.post(url, data=data, auth=HTTPBasicAuth(username, password))
```

如果 Apifox 中的真实接口路径与内置候选路径不同，复制 `.env.example` 为 `.env` 后修改：

```dotenv
WORKORDER_CUSTOMER_PATHS=/真实客户列表路径
WORKORDER_CONTACT_PATHS=/真实联系人列表路径
WORKORDER_TICKET_PATHS=/真实工单列表路径
```

## 使用

解析数据字典：

```powershell
uv run work-order-process dictionary
```

探测候选接口：

```powershell
uv run work-order-process probe
```

如果探测结果显示 `Invalid resource URI`，说明 Basic Auth 已带上，但接口路径不是当前候选值。需要从 Apifox 项目中复制真实路径，写入 `.env` 的 `WORKORDER_CUSTOMER_PATHS`、`WORKORDER_CONTACT_PATHS`、`WORKORDER_TICKET_PATHS`。

获取全部客户、联系人，并随机抽取 10 条 2025 年后的工单：

```powershell
uv run work-order-process run
```

输出文件位于 `output/`：

- `customers_raw.json`：客户原始数据
- `contacts_raw.json`：联系人原始数据
- `tickets_sample_raw.json`：2025 年后随机抽取的 10 条工单原始数据
- `customers.json`：字段中文化后的客户数据
- `contacts.json`：字段中文化后的联系人数据
- `tickets_sample.json`：随机 10 条工单，尽量补充联系人和客户信息
- `dictionary.json`：从 PDF 解析出的字段字典
