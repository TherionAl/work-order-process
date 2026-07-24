from __future__ import annotations

import logging
from pathlib import Path

from work_order_process import daily_runner


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_runner_suppresses_httpx_request_logs() -> None:
    daily_runner.configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING


def test_systemd_unit_runs_as_dedicated_user_and_restarts() -> None:
    text = (PROJECT_ROOT / "deploy" / "work-order-daily.service").read_text(
        encoding="utf-8"
    )
    assert "User=workorder" in text
    assert "Group=workorder" in text
    assert "Restart=on-failure" in text
    assert "EnvironmentFile=/etc/work-order-process/work-order.env" in text
    assert "WorkingDirectory=/opt/work_order_process" in text
    assert (
        "ExecStart=/opt/work_order_process/.venv/bin/python "
        "-m work_order_process.daily_runner"
    ) in text
    assert "NoNewPrivileges=true" in text


def test_logrotate_limits_daily_runner_log_retention() -> None:
    text = (PROJECT_ROOT / "deploy" / "work-order-daily.logrotate").read_text(
        encoding="utf-8"
    )
    assert "/var/log/work-order-process/*.log" in text
    assert "daily" in text
    assert "rotate 14" in text
    assert "compress" in text
    assert "create 0640 workorder workorder" in text
