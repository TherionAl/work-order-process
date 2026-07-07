# 组合版合并说明

## 目录

组合后的项目目录：

```text
D:\Users\python_project\work_order_process_v1.1
```

原始来源：

- 根目录版本：`D:\Users\python_project\work_order_process`
- Claude_code 版本：`D:\Users\python_project\Claude_code\work_order_process`

## 合并原则

后续方向以 Claude_code 版为主，原因是它更接近长期实用场景：

- MySQL 5 表结构。
- 工单主表和自定义字段明细按月分区。
- 客户、联系人、同步日志独立入库。
- 支持按月、按年批量同步。
- 支持并发抓取、批量提交、API QPS 限制。
- 保留原始 ID，并新增可读名称字段，便于分析和追溯。

根目录版本保留的能力：

- `monthly-tickets`：只导出月度工单合集，不拉取详情样本。
- `--detail-workers`：控制样本详情并发获取线程数。
- API 缓存返回防御性副本，避免调用方修改污染缓存。
- 结构化工单和结构化客户联系人相关测试继续保留。

## 已合入的 Claude_code 改良点

核心模块：

- `src/work_order_process/api.py`
- `src/work_order_process/config.py`
- `src/work_order_process/dictionary.py`
- `src/work_order_process/mysql_storage.py`
- `src/work_order_process/resolver.py`
- `src/work_order_process/cli.py`

测试：

- `tests/test_config.py`
- `tests/test_resolver.py`

文档：

- `README.md`
- `docs/mysql_schema.md`
- `docs/development_progress.md`

## 组合版额外修正

### CLI

组合版 CLI 同时支持轻量导出和数据湖入库：

```powershell
uv run work_order_process_v1.1 monthly-tickets --year 2026
uv run work_order_process_v1.1 run --year 2026 --detail-workers 4
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1
uv run work_order_process_v1.1 mysql-sync-log --log-limit 20
```

### API 缓存

Claude_code 版实体详情缓存会直接返回缓存对象。组合版改为返回深拷贝：

- 调用方修改返回值不会污染缓存。
- 保留实体缓存带来的性能收益。
- 根目录版 `test_api.py` 的缓存防御性测试继续通过。

### 解析语义

组合版沿用 Claude_code 版的入库友好语义：

- 原始 ID 字段保留。
- 新增可读名称字段。
- 自定义字段继续做选项值解析。
- 高频分析维度从 `custom_fields` 抽取到顶层字段。

示例：

```text
custUserId           -> 保留联系人 ID
cust_user_name       -> 新增联系人姓名
servicerUserId       -> 保留客服 ID
servicer_user_name   -> 新增客服姓名
ticketTemplateId     -> 保留模板 ID
ticket_template_name -> 新增模板名称
```

## 推荐实操流程

先验证配置：

```powershell
uv run work_order_process_v1.1 probe
```

先轻量导出一个月列表：

```powershell
uv run work_order_process_v1.1 monthly-tickets --year 2026 --month 6 --overwrite
```

再导出一个月样本详情：

```powershell
uv run work_order_process_v1.1 run --year 2026 --month 6 --limit-per-month 20 --detail-workers 2 --overwrite
```

初始化测试库：

```powershell
uv run work_order_process_v1.1 mysql-init
```

低并发试跑一个月入库：

```powershell
uv run work_order_process_v1.1 mysql-import-month --year 2025 --month 1 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

确认无误后扩大并发：

```powershell
uv run work_order_process_v1.1 mysql-import-year --year 2025 --max-workers 8 --batch-size 100 --api-rate-limit 10
```

查看同步结果：

```powershell
uv run work_order_process_v1.1 mysql-sync-log --log-limit 20
```

## 验证结果

组合版当前测试：

```text
44 passed
```

CLI help 已验证包含：

- `monthly-tickets`
- `template-samples`
- `mysql-import-month`
- `mysql-import-year`
- `mysql-import-customers`
- `mysql-import-contacts`
- `mysql-add-partitions`
- `mysql-sync-log`
- `--detail-workers`
- `--max-workers`
- `--batch-size`
- `--api-rate-limit`

## 后续建议

1. 在测试 MySQL 库完成一次 `mysql-init` 和单月低并发导入。
2. 确认 `ticket_detail_main` 的高频分析维度是否满足报表字段需求。
3. 后续可以拆分 `mysql_storage.py`，把 DDL、分区、导入、同步日志、行构建分到独立模块。
4. 正式跑全量前，先备份目标 MySQL 库并确认 `.env` 指向正确数据库。
