from __future__ import annotations

import logging
from pathlib import Path

from work_order_process import daily_runner

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_runner_suppresses_httpx_request_logs() -> None:
    daily_runner.configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING


def test_systemd_unit_runs_as_dedicated_user_and_restarts() -> None:
    text = (PROJECT_ROOT / "deploy" / "work-order-daily.service").read_text(encoding="utf-8")
    assert "User=workorder" in text
    assert "Group=workorder" in text
    assert "Restart=on-failure" in text
    assert "EnvironmentFile=/etc/work-order-process/work-order.env" in text
    assert "WorkingDirectory=/opt/work_order_process" in text
    assert (
        "ExecStart=/opt/work_order_process/.venv/bin/python -m work_order_process.daily_runner"
    ) in text
    assert "NoNewPrivileges=true" in text


def test_logrotate_limits_daily_runner_log_retention() -> None:
    text = (PROJECT_ROOT / "deploy" / "work-order-daily.logrotate").read_text(encoding="utf-8")
    assert "/var/log/work-order-process/*.log" in text
    assert "daily" in text
    assert "rotate 14" in text
    assert "compress" in text
    assert "create 0640 workorder workorder" in text


def test_mysql_backup_uses_protected_client_config_and_retention() -> None:
    text = (PROJECT_ROOT / "scripts" / "backup_mysql.sh").read_text(encoding="utf-8")
    assert "--defaults-extra-file=/etc/work-order-process/mysql-backup.cnf" in text
    assert "/var/backups/work-order-process" in text
    assert "mysqldump" in text
    assert "gzip" in text
    assert "-mtime" in text
    assert "--password" not in text
    assert "MYSQL_PASSWORD" not in text


def test_mysql_backup_timer_is_persistent_and_daily() -> None:
    service = (PROJECT_ROOT / "deploy" / "work-order-backup.service").read_text(encoding="utf-8")
    timer = (PROJECT_ROOT / "deploy" / "work-order-backup.timer").read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "User=workorder" in service
    assert "Group=workorder" in service
    assert "OnCalendar=*-*-* 01:00:00" in timer
    assert "Persistent=true" in timer


def test_hubei_weekly_quality_timer_runs_four_segments_and_is_persistent() -> None:
    service = (PROJECT_ROOT / "deploy" / "work-order-hubei-quality-weekly.service").read_text(
        encoding="utf-8"
    )
    timer = (PROJECT_ROOT / "deploy" / "work-order-hubei-quality-weekly.timer").read_text(
        encoding="utf-8"
    )

    assert "Type=oneshot" in service
    assert "--period weekly --apply --allow-catch-up" in service
    assert "OnCalendar=*-*-08,15,22,29 05:17:00" in timer
    assert "Persistent=true" in timer


def test_hubei_monthly_quality_timer_runs_on_first_and_is_persistent() -> None:
    service = (PROJECT_ROOT / "deploy" / "work-order-hubei-quality-monthly.service").read_text(
        encoding="utf-8"
    )
    timer = (PROJECT_ROOT / "deploy" / "work-order-hubei-quality-monthly.timer").read_text(
        encoding="utf-8"
    )

    assert "Type=oneshot" in service
    assert "--period monthly --apply --allow-catch-up" in service
    assert "OnCalendar=*-*-01 05:17:00" in timer
    assert "Persistent=true" in timer


def test_hubei_quality_services_share_one_cross_process_lock() -> None:
    weekly = (PROJECT_ROOT / "deploy" / "work-order-hubei-quality-weekly.service").read_text(
        encoding="utf-8"
    )
    monthly = (PROJECT_ROOT / "deploy" / "work-order-hubei-quality-monthly.service").read_text(
        encoding="utf-8"
    )
    lock_argument = "/usr/bin/flock --wait 7200 /opt/work_order_process/output/.hubei-quality.lock"

    assert lock_argument in weekly
    assert lock_argument in monthly
