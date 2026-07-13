"""APScheduler 常驻进程：处理所有定时同步任务。

启动: uv run python -m work_order_process.daily_runner

同步策略：
- 每天 02:17：当月 + 前溯 3 个月（覆盖近 ~90-120 天内可能被修改的工单）。
  工单创建后短期内 updateDT 会变化（状态流转、解决/关单等），90 天内大概率还在变动，
  因此每日轮询检测 source_updated_at 并 upsert 有变化的工单。
- 每周日 03:17：全量导入客户/公司 + 联系人。
- 每月 1 号 04:17：创建后续 6 个月分区 + 刷新当年 90 天前的老月份。
  90 天前的工单基本趋于稳定，每月补一次即可；按 cutoff 切割当年/去年范围。

所有月份导入共用 import_month_tickets_to_mysql，它按 createDT 月份搜索列表、
对比 DB 中 source_updated_at 与 API updateDT，只对变化的工单重新拉取详情，
因此重复执行同一月份的实际 API 调用量极低。
"""

from __future__ import annotations

import logging
import signal
import sys
from datetime import datetime, timedelta

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
    """同步单个自然月的工单。

    当月：新增 + 按 updateDT 检测变化更新已有工单。
    前溯月份（delta=1..3）：按 updateDT 检测数据库中已有工单的 updateDT 是否变化，
    变化的重新拉取详情并 upsert；未变化的自动 skip。
    """
    logger.info("开始同步 %d-%02d 工单", year, month)
    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()
            report = import_month_tickets_to_mysql(
                settings.mysql, dictionary, client,
                year=year, month=month,
                max_workers=8, batch_size=100, api_rate_limit=10,
            )
        logger.info(
            "%d-%02d 完成: imported=%d updated=%d skipped=%d failed=%d",
            year, month,
            report.get("imported", 0), report.get("updated", 0),
            report.get("skipped", 0), report.get("failed", 0),
        )
    except Exception:
        logger.exception("%d-%02d 同步异常", year, month)


def job_sync_tickets_daily() -> None:
    """每天 02:17：导入当月 + 前溯 3 个月（覆盖近 ~90-120 天内）的工单。"""
    logger.info("定时任务: daily_sync_tickets")
    now = datetime.now()
    # delta=0 当月，delta=1..3 前溯 3 个月；共 4 个月 ≈ 90-120 天
    for delta in [0, 1, 2, 3]:
        d = now - relativedelta(months=delta)
        sync_tickets_for_month(d.year, d.month)


def job_sync_customers_contacts() -> None:
    """每周日 03:17：导入客户/公司 + 联系人。"""
    logger.info("定时任务: sync_customers_contacts")
    with WorkOrderClient(settings) as client:
        client.authenticate()
        import_customers_to_mysql(settings.mysql, client)
        import_contacts_to_mysql(settings.mysql, client)


def job_monthly_maintenance() -> None:
    """每月 1 号 04:17：刷新当年 90 天前的老月份 + 创建后续 6 个月分区。

    cutoff = 今天 - 90 天。
    - 不跨年：刷新当年 1 月 ~ (cutoff月份-1) 的老数据。
    - 跨年：刷新去年 (cutoff月份+1) ~ 12 月的老数据。
    90 天前的工单更新频率低，每月补一次即可。
    """
    logger.info("定时任务: monthly_maintenance (老月份刷新 + 分区维护)")
    now = datetime.now()
    cutoff = now - timedelta(days=90)

    if cutoff.year == now.year:
        # 不跨年：当年 1 月 ~ cutoff月份-1（这些在 90 天前，不被每日覆盖）
        for month in range(1, cutoff.month):
            sync_tickets_for_month(now.year, month)
    else:
        # 跨年：去年 cutoff月份+1 ~ 12 月（去年最后几月在 90 天前）
        for month in range(cutoff.month + 1, 13):
            sync_tickets_for_month(cutoff.year, month)

    # 创建后续 6 个月分区
    months = generate_months_ahead(6)
    add_future_partitions(settings.mysql, months)
    logger.info("monthly_maintenance 完成: 分区已创建")


def main() -> None:
    # 注册任务
    sched.add_job(
        job_sync_tickets_daily,
        CronTrigger(hour=2, minute=17),
        id="sync_tickets_daily",
        name="工单日报同步(当月+前3月)",
    )
    sched.add_job(
        job_sync_customers_contacts,
        CronTrigger(day_of_week="sun", hour=3, minute=17),
        id="sync_customers_contacts",
        name="客户与联系人同步",
    )
    sched.add_job(
        job_monthly_maintenance,
        CronTrigger(day=1, hour=4, minute=17),
        id="monthly_maintenance",
        name="月度维护(老月份刷新+分区)",
    )

    # 正常退出
    def shutdown(signum, frame):
        logger.info("收到信号 %s，停止调度器", signum)
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("调度器启动 (Asia/Shanghai)。")
    logger.info("任务规则:")
    logger.info("  02:17 每天  = 当月 + 前溯 3 个月 (~90天)")
    logger.info("  03:17 周日  = 全量客户/联系人")
    logger.info("  04:17 每月1号 = 当年90天前老月份 + 分区维护")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
