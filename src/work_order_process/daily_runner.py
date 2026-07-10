"""APScheduler 常驻进程：处理所有定时同步任务。

启动: uv run python -m work_order_process.daily_runner
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dateutil.relativedelta import relativedelta

from .api import WorkOrderClient
from .config import load_settings
from .dictionary import DataDictionary
from .mysql_storage import (
    add_future_partitions,
    generate_months_ahead,
    import_contacts_to_mysql,
    import_customers_to_mysql,
    import_month_tickets_to_mysql,
)
from .time_metrics import DEFAULT_CALENDAR_PATH, DEFAULT_METRICS_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("daily_runner")

settings = load_settings()
dictionary = DataDictionary.from_pdf(settings.dictionary_path)
sched = BlockingScheduler(timezone="Asia/Shanghai")


def sync_tickets_for_month(year: int, month: int) -> None:
    logger.info("开始同步 %d-%02d 工单", year, month)
    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()
            report = import_month_tickets_to_mysql(
                settings.mysql, dictionary, client,
                year=year, month=month,
                max_workers=8, batch_size=100, api_rate_limit=10,
            )
        logger.info("%d-%02d 完成: imported=%d updated=%d skipped=%d failed=%d",
                     year, month,
                     report.get("imported", 0), report.get("updated", 0),
                     report.get("skipped", 0), report.get("failed", 0))
    except Exception:
        logger.exception("%d-%02d 同步异常", year, month)


def job_sync_tickets() -> None:
    """每天 02:17：导入当月 + 前2个月工单。"""
    logger.info("定时任务: sync_tickets")
    now = datetime.now()
    for delta in [0, 1, 2]:
        d = now - relativedelta(months=delta)
        sync_tickets_for_month(d.year, d.month)


def job_sync_customers_contacts() -> None:
    """每周日 03:17：导入客户/公司 + 联系人。"""
    logger.info("定时任务: sync_customers_contacts")
    with WorkOrderClient(settings) as client:
        client.authenticate()
        import_customers_to_mysql(settings.mysql, client)
        import_contacts_to_mysql(settings.mysql, client)


def job_add_partitions() -> None:
    """每月 1 号 04:17：创建后续 6 个月分区。"""
    logger.info("定时任务: add_partitions")
    months = generate_months_ahead(6)
    add_future_partitions(settings.mysql, months)


def main() -> None:
    # 注册任务
    sched.add_job(
        job_sync_tickets,
        CronTrigger(hour=2, minute=17),
        id="sync_tickets",
        name="工单同步(当月+前2月)",
    )
    sched.add_job(
        job_sync_customers_contacts,
        CronTrigger(day_of_week="sun", hour=3, minute=17),
        id="sync_customers_contacts",
        name="客户与联系人同步",
    )
    sched.add_job(
        job_add_partitions,
        CronTrigger(day=1, hour=4, minute=17),
        id="add_partitions",
        name="月度分区维护",
    )

    # 正常退出
    def shutdown(signum, frame):
        logger.info("收到信号 %s，停止调度器", signum)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("调度器启动 (Asia/Shanghai)。")
    logger.info("任务: 每天02:17工单 / 每周日03:17客户联系人 / 每月1号04:17分区")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
