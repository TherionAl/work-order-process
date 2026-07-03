# 工单数据获取及解析处理

本项目当前只保留 2025 年工单月度导出流程：

1. 按工单创建时间导出 2025 年 1-12 月每个月的工单合集。
2. 从每个月的工单合集里抽 3 条工单。
3. 对抽到的 3 条工单详情生成三份 JSON：原始值、value 替换后、字段中文化后。

## 环境

```powershell
uv sync
```

配置优先级：

1. `.env`
2. `agents.md` 中的 `USERNAME`、`PASSWORD`、实际项目地址前缀

接口认证使用 HTTP Basic Auth，项目中由 `httpx.BasicAuth` 处理。

## 运行

正式导出 2025 年数据：

```powershell
uv run work-order-process run
```

调试时可以限制每个月只取少量列表记录，例如每月最多 10 条：

```powershell
uv run work-order-process run --limit-per-month 10 --overwrite
```

如果中途超时，可以按月份续跑。例如只补 2025 年 3 月：

```powershell
uv run work-order-process run --month 3
```

默认分页大小是 `5000`，这是当前接口已验证可用的较大分页；`10000` 会触发接口 500。

按模板分别抽样某个月的工单，例如从 2026 年 6 月每个模板随机抽 3 条：

```powershell
uv run work-order-process template-samples --year 2026 --month 6 --sample-size 3 --seed 202606 --overwrite
```

输出到：

```text
output/2026_06_template_sample_details/
  2026-06_template_sample_details_raw.json
  2026-06_template_sample_details_value_resolved.json
  2026-06_template_sample_details_chinese.json
```

这三份文件的结构与 `output/2025_monthly_sample_details/` 中的样本详情文件一致。

如果只想检查认证和工单列表接口：

```powershell
uv run work-order-process probe
```

## MySQL 入库

本机 MySQL 配置写到 `.env`，不要把真实密码提交进 Git：

```dotenv
WORKORDER_MYSQL_HOST=127.0.0.1
WORKORDER_MYSQL_PORT=3306
WORKORDER_MYSQL_USER=root
WORKORDER_MYSQL_PASSWORD=你的本机MySQL密码
WORKORDER_MYSQL_DATABASE=work_order
```

初始化数据库和两张表：

```powershell
uv run work-order-process mysql-init
```

拉取单条工单详情并写入 MySQL：

```powershell
uv run work-order-process mysql-import-ticket --ticket-id 22256891
```

当前只建两张表：

- `ticket_detail_main`：一条工单一行，保存顶层字段。
- `ticket_detail_custom_fields`：一条自定义字段一行，保存 `custom_fields` 动态字段。

详细表结构和更新策略见 `docs/mysql_schema.md`。

## 输出

月度工单合集：

```text
output/2025_monthly_tickets/
  2025-01_tickets.json
  2025-02_tickets.json
  ...
  2025-12_tickets.json
```

每个文件包含：

- `month`：月份
- `declared_count`：接口返回的该月总量
- `fetched_count`：实际保存数量
- `ticket_ids`：该月工单 ID
- `tickets`：搜索接口返回的该月工单列表合集

每月 3 条样本详情：

```text
output/2025_monthly_sample_details/
  2025-01_sample_details_raw.json
  2025-01_sample_details_value_resolved.json
  2025-01_sample_details_chinese.json
  ...
```

- `raw`：工单详情接口原始返回值
- `value_resolved`：英文 key 保留，尽量把 ID、枚举、自定义字段选项替换成可读中文
- `chinese`：在 value 替换后，再用数据字典把 key 中文化

抽样默认使用固定随机种子 `2025`，所以同一份月度合集多次运行会抽到相同样本；可通过 `--seed` 修改。
