# 生产运行说明

## 运行边界

仓库中的 `deploy/` 文件是生产配置模板，不会自动修改服务器。安装前必须确认服务器路径、虚拟环境、现有备份和账号权限。

生产环境使用：

- Linux 用户和用户组：`workorder`
- 项目目录：`/opt/work_order_process`
- 环境文件：`/etc/work-order-process/work-order.env`
- 日志目录：`/var/log/work-order-process`
- 备份目录：`/var/backups/work-order-process`

服务和 MySQL 日常账号都不得使用 root。

## 文件权限

```bash
chown -R workorder:workorder /opt/work_order_process/output
chown -R workorder:workorder /var/log/work-order-process
chmod 0700 /etc/work-order-process
chmod 0600 /etc/work-order-process/work-order.env
```

环境文件至少包含 `.env.example` 中的 API 和 MySQL 参数。真实值不得提交到 Git。

## daily_runner

部署依赖时使用 `uv sync --all-groups --locked`，服务运行时直接调用
`/opt/work_order_process/.venv/bin/python`，避免调度器启动时再次解析或修改环境。配置文件：

- `deploy/work-order-daily.service`
- `deploy/work-order-daily.logrotate`

安装命令将在服务器变更确认后执行。目标行为：

- 非 root 用户运行。
- 进程异常退出后自动重启。
- 同一 systemd unit 只运行一个调度器。
- HTTP 请求明细不写 INFO 日志。
- 日志每日轮转并保留 14 份。

## 健康检查

部署后至少核对：

```bash
systemctl is-active work-order-daily.service
systemctl status work-order-daily.service --no-pager
journalctl -u work-order-daily.service -n 100 --no-pager
```

数据库侧检查最近的 `sync_task_log`，不能只以 APScheduler 的 executed successfully 作为成功依据。

## 数据库备份

备份组件：

- `scripts/backup_mysql.sh`
- `deploy/work-order-backup.service`
- `deploy/work-order-backup.timer`
- `deploy/mysql-backup.cnf.example`

将 MySQL 客户端配置保存为
`/etc/work-order-process/mysql-backup.cnf`，文件所有者为 `workorder`，权限为
`0600`。备份账号只授予 `work_order_datalake` 的备份所需只读权限，不在命令行或
systemd unit 中写密码。

默认每天 01:00 执行，结果写入 `/var/backups/work-order-process`，保留 14 天。
可通过 service 的 `Environment=` 覆盖 `DATABASE_NAME`、`BACKUP_DIR` 和
`RETENTION_DAYS`。

启用前先手动运行并核对：

```bash
systemctl start work-order-backup.service
systemctl status work-order-backup.service --no-pager
gzip -t /var/backups/work-order-process/work_order_datalake_*.sql.gz
systemctl enable --now work-order-backup.timer
systemctl list-timers work-order-backup.timer --no-pager
```

上线后应定期在隔离数据库中执行恢复演练。只有文件存在和 `gzip -t` 成功，不能证明
SQL 内容可完整恢复。

## 外部变更清单

以下操作会修改服务器、账号或远程仓库，本次本地代码修复不自动执行：

1. 轮换 API 密码，并更新 `/etc/work-order-process/work-order.env`。
2. 创建 `workorder` Linux 用户及最小权限 MySQL 应用、备份账号。
3. 将环境文件和 MySQL 备份配置设为 `0600`，相关目录设为 `0700`。
4. 安装并启用 `work-order-daily.service` 和 logrotate 配置。
5. 确认现有基础设施备份后，再启用 `work-order-backup.timer`。
6. 在 Linux 上执行 `bash -n scripts/backup_mysql.sh` 和一次隔离恢复演练。
7. 修复本机 SSH `KexAlgorithms` 兼容配置。
8. 评估历史凭据风险，明确确认后再决定是否重写 Git 历史和强制推送。
9. 将同一 Git 提交部署到服务器，重启服务后执行数据库只读验收。
