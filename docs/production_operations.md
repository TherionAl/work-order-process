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
