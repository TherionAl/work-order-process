"""MySQL-backed sync task logging."""

from __future__ import annotations

import json
from typing import Any

import pymysql

from .config import MySQLConfig


MAX_SYNC_LOG_READ_LIMIT = 1000

SYNC_TASK_LOG_DDL = """
CREATE TABLE IF NOT EXISTS sync_task_log (
  id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  task_type VARCHAR(50) NOT NULL COMMENT '任务类型 ticket_detail/customer/contact',
  target_year SMALLINT NULL COMMENT '目标年份',
  target_month TINYINT NULL COMMENT '目标月份',
  target_month_label VARCHAR(7) NULL COMMENT '目标年月 YYYY-MM',
  status VARCHAR(20) NOT NULL COMMENT '任务状态 running/success/failed/partial',
  total_count INT NOT NULL DEFAULT 0 COMMENT '应处理数量',
  success_count INT NOT NULL DEFAULT 0 COMMENT '成功数量',
  failed_count INT NOT NULL DEFAULT 0 COMMENT '失败数量',
  skipped_count INT NOT NULL DEFAULT 0 COMMENT '跳过数量',
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开始时间',
  finished_at TIMESTAMP NULL COMMENT '结束时间',
  duration_seconds INT NULL COMMENT '耗时秒数',
  error_message TEXT NULL COMMENT '错误摘要',
  extra_json JSON NULL COMMENT '扩展信息，如失败ID列表',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '日志创建时间',
  PRIMARY KEY (id),
  KEY idx_task_month (task_type, target_month_label),
  KEY idx_status (status),
  KEY idx_started_at (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='同步任务日志表'
"""


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def write_sync_log(
    config: MySQLConfig,
    *,
    task_type: str,
    target_year: int | None = None,
    target_month: int | None = None,
    month_label: str,
    status: str,
    total_count: int = 0,
    success_count: int = 0,
    failed_count: int = 0,
    skipped_count: int = 0,
    duration_seconds: int | None = None,
    error_message: str | None = None,
    extra_json: dict[str, Any] | None = None,
) -> None:
    """Write one sync task log row."""

    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sync_task_log (task_type, target_year, target_month, target_month_label, "
                "status, total_count, success_count, failed_count, skipped_count, "
                "finished_at, duration_seconds, error_message, extra_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)",
                (
                    task_type,
                    target_year,
                    target_month,
                    month_label,
                    status,
                    total_count,
                    success_count,
                    failed_count,
                    skipped_count,
                    duration_seconds,
                    error_message,
                    _json_or_none(extra_json),
                ),
            )


def read_sync_logs(config: MySQLConfig, limit: int) -> list[dict[str, Any]]:
    """Return the most recent bounded sync task logs."""

    if not 0 < limit <= MAX_SYNC_LOG_READ_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {MAX_SYNC_LOG_READ_LIMIT}"
        )

    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, task_type, target_year, target_month, target_month_label, "
                "status, total_count, success_count, failed_count, skipped_count, "
                "started_at, finished_at, duration_seconds, error_message, extra_json, created_at "
                "FROM sync_task_log ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return list(cursor.fetchall())
