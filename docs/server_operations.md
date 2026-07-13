# 服务器运行手册

本文档用于操作部署在服务器上的工单采集与 MySQL 入库项目。文档中的
`work_order_process` 是服务器 `/opt/work_order_process` 的 CLI 名称；本地开发
环境的命令名可能不同，不能直接照搬。

## 登录与目录

本地 SSH 配置已设置私钥时：

```bash
ssh 172.18.169.231
cd /opt/work_order_process
```

不要在命令行、日志或本文档中记录服务器密码、私钥内容或 `.env` 的实际值。
所有项目命令都应在 `/opt/work_order_process` 目录中执行。

## 健康检查

先确认运行环境、配置文件、接口和调度器：

```bash
cd /opt/work_order_process
uv --version
test -f .env && echo '.env ready'
uv run work_order_process probe
ps -ef | grep '[d]aily_runner'
tail -n 100 logs/daily_runner_stdout.log
```

`probe` 成功后再执行导入。`daily_runner` 应为一个常驻 Python 进程；其输出写入
`logs/daily_runner_stdout.log`。

## 自动同步

服务器上的 `daily_runner` 使用 `Asia/Shanghai` 时区，任务规则如下：

| 时间 | 工作内容 |
|---|---|
| 每天 02:17 | 同步当月及前 3 个月的工单，按 API `updateDT` 做增量更新。 |
| 每周日 03:17 | 同步客户、公司与联系人。 |
| 每月 1 日 04:17 | 刷新当年较早月份、创建未来 6 个月分区。 |

检查最近一次任务是否完成：

```bash
tail -n 200 logs/daily_runner_stdout.log
uv run work_order_process mysql-sync-log --log-limit 20
```

如果调度器未运行，先检查日志和项目配置；不要在已有 `daily_runner` 进程时再启动
第二个实例。手动启动命令仅用于明确的恢复操作：

```bash
uv run python -m work_order_process.daily_runner
```

## 手动导入

先执行最小范围操作。已知失败工单必须先单条补录，避免整月重复刷新。

### 单条工单补录

```bash
uv run work_order_process mysql-import-ticket --ticket-id <ticket_id>
```

成功时会显示 `main_rows` 和 `custom_field_rows`。随后用同步日志或数据库查询确认
记录存在。

### 单月导入

```bash
uv run work_order_process mysql-import-month --year 2026 --month 6
```

首次排障或 API 压力较大时，使用低并发参数：

```bash
uv run work_order_process mysql-import-month --year 2026 --month 6 --max-workers 2 --batch-size 20 --api-rate-limit 3
```

该命令会先比较列表中的 `updateDT` 与数据库 `source_updated_at`，再获取缺失或变化的
详情。若存在大量历史数据，预筛选本身也可能需要数分钟。

### 全年导入

```bash
uv run work_order_process mysql-import-year --year 2026
```

全年导入会对每月列表执行检查，耗时和 API 负载较高。仅在明确需要全年补录或刷新时
执行；不要把它作为处理少量失败 ID 的默认方式。

### 客户、联系人与分区

```bash
uv run work_order_process mysql-import-customers
uv run work_order_process mysql-import-contacts
uv run work_order_process mysql-add-partitions --months-ahead 6
```

初始化空库时可执行 `uv run work_order_process mysql-init`。已存在生产库不需要重复
初始化。

## 数据对账

先查看项目同步日志：

```bash
uv run work_order_process mysql-sync-log --log-limit 20
```

按月核对主表行数时，使用已有的 `create_month_label` 索引：

```sql
SELECT COUNT(*) AS ticket_count
FROM ticket_detail_main FORCE INDEX (idx_create_month_label)
WHERE create_month_label = '2026-06';
```

在服务器 MySQL 客户端中执行该 SQL：

```bash
mysql -u root -p work_order_datalake
```

输入密码后粘贴查询。不要对 `ticket_detail_custom_fields` 执行无条件的
`COUNT(*)`；该表规模较大，完整扫描可能长期占用数据库。

同步日志中的 `total_count` 与主表月度行数一致、`failed_count` 为 `0` 时，该月可视为
完整。若有缺口，先从 `sync_task_log.extra_json` 读取 `failed_ids`，然后对每个 ID 执行
单条补录。

## 失败工单处理

| 现象 | 处理方式 |
|---|---|
| `Ticket detail not found` | 源系统不再提供该详情，重复导入不会补齐；需要源系统恢复或导出。 |
| `Forbidden` 或无查看权限 | 当前账号无权读取该工单；需要调整源系统权限。不要把错误响应写入业务字段。 |
| MySQL 字段长度错误 | 先检查 API 返回是否为错误文本，再决定是否需要调整数据模型。 |
| 单月大量更新耗时较长 | 检查是否有大量 `updateDT` 变化；不要同时再启动另一条同月导入。 |

处理前检查是否已有导入进程：

```bash
ps -ef | grep '[m]ysql-import'
```

若存在任务，等待其结束或确认其具体状态后再处理，避免重复 API 调用和并发写入。

## 安全边界

- `mysql-drop-tables` 会删除所有工单相关表，只能在明确的恢复方案和备份确认后执行。
- 不要在共享终端历史、脚本或文档中保存 `.env` 内容。
- 不要为了凑齐行数而为无权限或不存在的工单创建空记录；这会破坏数据完整性。
- 批量导入完成后必须检查同步日志、失败 ID 和月度主表行数。
