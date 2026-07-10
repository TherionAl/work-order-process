#!/bin/bash
# 每周同步客户和联系人
export PATH="/opt/python314/bin:/usr/local/bin:$PATH"
cd /opt/work_order_process_v1.1
/usr/local/bin/uv run --python /opt/python314/bin/python3.14 python main.py mysql-import-customers >> /var/log/workorder-cc.log 2>&1
/usr/local/bin/uv run --python /opt/python314/bin/python3.14 python main.py mysql-import-contacts >> /var/log/workorder-cc.log 2>&1
