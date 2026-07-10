#!/bin/bash
# 每日定时同步当月工单到服务器 MySQL
# 通过 crontab 每天凌晨 2:17 执行
export PATH="/opt/python314/bin:/usr/local/bin:$PATH"
cd /opt/work_order_process_v1.1
/usr/local/bin/uv run --python /opt/python314/bin/python3.14 python main.py mysql-import-month --month $(date +%%m) >> /var/log/workorder-sync.log 2>&1
