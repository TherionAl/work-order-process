"""MySQL 建表与工单详情入库 — 5 表分区模型。

work_order_datalake 数据仓库包含以下 5 张表：

1. ticket_detail_main      工单主表（按月 create_dt 分区，PK=(ticket_id, create_dt)）
2. ticket_detail_custom_fields  工单自定义字段明细（按月分区，PK=(ticket_id, field_order, create_dt)，无 FK）
3. customers               客户/公司表（普通表，PK=customer_id）
4. contacts                联系人表（普通表，PK=contact_id）
5. sync_task_log           同步任务日志表（普通表，自增 PK）

设计要点：
- 两张工单表按月分区，便于大规模数据的快速查询与归档；
- 分区表不用 MySQL 外键，靠索引 + 程序逻辑保证关联；
- 高频分析维度冗余到 ticket_detail_main，低频动态字段保留在 ticket_detail_custom_fields；
- 导入期间通过外部 SET GLOBAL innodb_flush_log_at_trx_commit=2 加速 fsync。
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .api import ApiError, WorkOrderClient
from .config import MySQLConfig
from .dictionary import DataDictionary
from .resolver import TicketFieldResolver, _split_id_list, resolve_ticket_detail_values


# ---------------------------------------------------------------------------
# ticket_detail_main 字段映射：API 顶层字段 → DB 列名
# ---------------------------------------------------------------------------

MAIN_FIELD_COLUMN_MAP = {
    "ticketId": "ticket_id",
    "subject": "subject",
    "descript": "descript",
    "custUserId": "cust_user_id",
    "servicerUserId": "servicer_user_id",
    "ccUserIdList": "cc_user_id_list",
    "ticketType": "ticket_type",
    "priorityLevel": "priority_level",
    "tagList": "tag_list",
    "ticketStatus": "ticket_status",
    "createDT": "create_dt",
    "updateDT": "source_updated_at",
    "solveDT": "solve_dt",
    "waitDT": "wait_dt",
    "openDT": "open_dt",
    "closeDT": "close_dt",
    "servicerGroupId": "servicer_group_id",
    "createrId": "creater_id",
    "agentId": "agent_id",
    "ticketSource": "ticket_source",
    "ticketTemplateId": "ticket_template_id",
    "ccGroupIdList": "cc_group_id_list",
    "customTemplateId": "custom_template_id",
    "createrType": "creater_type",
    "currentNodeField": "current_node_field",
    "currentNodeFieldValue": "current_node_field_value",
    "nodeFieldIntoTime": "node_field_into_time",
    "queryIDs": "query_ids",
    "workflow_node_id": "workflow_node_id",
    "workflow_id": "workflow_id",
    "isDeleted": "is_deleted",
    "deleterId": "deleter_id",
    "deleteDT": "delete_dt",
    "descriptattachments": "descript_attachments",
    # 以下为 resolver 提供的附加字段（不在 API 顶层中，由 *_name 后缀注入）
    "cust_user_name": "cust_user_name",
    "company_id": "company_id",
    "company_name": "company_name",
    "servicer_user_name": "servicer_user_name",
    "creater_name": "creater_name",
    "servicer_group_name": "servicer_group_name",
    "ticket_template_name": "ticket_template_name",
}

# 由 resolver 提供的分析维度字段（从 custom_fields 中提取）
ANALYTIC_COLUMNS = [
    "province",
    "city",
    "district",
    "region_text",
    "ticket_category",
    "product_line",
    "module_name",
    "problem_type",
    "customer_type",
    "customer_industry",
    "department_id",
    "department_name",
    "current_node_name",
    "current_node_status",
    "current_node_started_at",
    "current_node_duration_seconds",
]

DATETIME_COLUMNS = {
    "create_dt",
    "source_updated_at",
    "solve_dt",
    "wait_dt",
    "open_dt",
    "close_dt",
    "node_field_into_time",
    "delete_dt",
    "current_node_started_at",
}

JSON_COLUMNS = {"descript_attachments"}


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

PARTITION_CLAUSE_2025_2026 = """
PARTITION BY RANGE COLUMNS(create_dt) (
  PARTITION p202501 VALUES LESS THAN ('2025-02-01'),
  PARTITION p202502 VALUES LESS THAN ('2025-03-01'),
  PARTITION p202503 VALUES LESS THAN ('2025-04-01'),
  PARTITION p202504 VALUES LESS THAN ('2025-05-01'),
  PARTITION p202505 VALUES LESS THAN ('2025-06-01'),
  PARTITION p202506 VALUES LESS THAN ('2025-07-01'),
  PARTITION p202507 VALUES LESS THAN ('2025-08-01'),
  PARTITION p202508 VALUES LESS THAN ('2025-09-01'),
  PARTITION p202509 VALUES LESS THAN ('2025-10-01'),
  PARTITION p202510 VALUES LESS THAN ('2025-11-01'),
  PARTITION p202511 VALUES LESS THAN ('2025-12-01'),
  PARTITION p202512 VALUES LESS THAN ('2026-01-01'),
  PARTITION p202601 VALUES LESS THAN ('2026-02-01'),
  PARTITION p202602 VALUES LESS THAN ('2026-03-01'),
  PARTITION p202603 VALUES LESS THAN ('2026-04-01'),
  PARTITION p202604 VALUES LESS THAN ('2026-05-01'),
  PARTITION p202605 VALUES LESS THAN ('2026-06-01'),
  PARTITION p202606 VALUES LESS THAN ('2026-07-01'),
  PARTITION p202607 VALUES LESS THAN ('2026-08-01'),
  PARTITION p202608 VALUES LESS THAN ('2026-09-01'),
  PARTITION p202609 VALUES LESS THAN ('2026-10-01'),
  PARTITION p202610 VALUES LESS THAN ('2026-11-01'),
  PARTITION p202611 VALUES LESS THAN ('2026-12-01'),
  PARTITION p202612 VALUES LESS THAN ('2027-01-01'),
  PARTITION pmax    VALUES LESS THAN (MAXVALUE)
)
"""

TICKET_DETAIL_MAIN_DDL = f"""
CREATE TABLE IF NOT EXISTS ticket_detail_main (
  ticket_id BIGINT NOT NULL COMMENT '工单ID',
  subject VARCHAR(1000) NULL COMMENT '标题',
  descript MEDIUMTEXT NULL COMMENT '描述',
  cust_user_id VARCHAR(255) NULL COMMENT '联系人ID',
  cust_user_name VARCHAR(255) NULL COMMENT '联系人姓名',
  company_id VARCHAR(255) NULL COMMENT '公司/客户ID（来自联系人详情）',
  company_name VARCHAR(500) NULL COMMENT '公司名称（来自公司详情）',
  servicer_user_id VARCHAR(255) NULL COMMENT '客服ID',
  servicer_user_name VARCHAR(255) NULL COMMENT '客服姓名',
  cc_user_id_list TEXT NULL COMMENT '抄送客服ID列表',
  ticket_type VARCHAR(100) NULL COMMENT '工单类型',
  priority_level VARCHAR(100) NULL COMMENT '优先级',
  tag_list TEXT NULL COMMENT '标签',
  ticket_status VARCHAR(100) NULL COMMENT '工单状态',
  create_dt DATETIME NOT NULL COMMENT '创建时间（分区键）',
  source_updated_at DATETIME NULL COMMENT '来源更新时间（API updateDT，用于增量判断）',
  solve_dt DATETIME NULL COMMENT '解决时间',
  wait_dt DATETIME NULL COMMENT '等待时间',
  open_dt DATETIME NULL COMMENT '开启时间',
  close_dt DATETIME NULL COMMENT '关闭时间',
  servicer_group_id VARCHAR(255) NULL COMMENT '客服组ID',
  servicer_group_name VARCHAR(255) NULL COMMENT '客服组名称',
  creater_id VARCHAR(255) NULL COMMENT '创建人ID',
  creater_name VARCHAR(255) NULL COMMENT '创建人姓名',
  agent_id VARCHAR(255) NULL COMMENT '服务商/代理商ID',
  ticket_source VARCHAR(100) NULL COMMENT '工单来源',
  ticket_template_id VARCHAR(255) NULL COMMENT '工单模板ID',
  ticket_template_name VARCHAR(255) NULL COMMENT '工单模板名称',
  cc_group_id_list TEXT NULL COMMENT '抄送客服组ID列表',
  custom_template_id VARCHAR(255) NULL COMMENT '自定义模板ID',
  creater_type VARCHAR(100) NULL COMMENT '创建人类型',
  current_node_field VARCHAR(255) NULL COMMENT '当前流程节点字段',
  current_node_field_value VARCHAR(255) NULL COMMENT '当前流程节点值',
  node_field_into_time DATETIME NULL COMMENT '进入节点时间',
  query_ids TEXT NULL COMMENT '查询ID集合',
  workflow_node_id VARCHAR(255) NULL COMMENT '工作流节点ID',
  workflow_id VARCHAR(255) NULL COMMENT '工作流ID',
  is_deleted VARCHAR(50) NULL COMMENT '是否删除',
  deleter_id VARCHAR(255) NULL COMMENT '删除人ID',
  delete_dt DATETIME NULL COMMENT '删除时间',
  descript_attachments JSON NULL COMMENT '描述附件JSON',
  create_year SMALLINT NULL COMMENT '创建年份，从 create_dt 派生',
  create_month TINYINT NULL COMMENT '创建月份，从 create_dt 派生',
  create_month_label VARCHAR(7) NULL COMMENT '创建年月 YYYY-MM，从 create_dt 派生',
  last_sync_at TIMESTAMP NULL COMMENT '最近同步时间',
  sync_status VARCHAR(20) NULL COMMENT '同步状态 success/skipped/failed',
  sync_error TEXT NULL COMMENT '同步错误信息',
  province VARCHAR(50) NULL COMMENT '省份',
  city VARCHAR(50) NULL COMMENT '城市',
  district VARCHAR(50) NULL COMMENT '区县',
  region_text VARCHAR(255) NULL COMMENT '地区原始文本',
  ticket_category VARCHAR(50) NULL COMMENT '工单类别',
  product_line VARCHAR(255) NULL COMMENT '产品线',
  module_name VARCHAR(255) NULL COMMENT '模块名称',
  problem_type VARCHAR(255) NULL COMMENT '问题类型',
  customer_type VARCHAR(100) NULL COMMENT '客户类型',
  customer_industry VARCHAR(100) NULL COMMENT '客户行业',
  department_id VARCHAR(255) NULL COMMENT '内部部门ID',
  department_name VARCHAR(255) NULL COMMENT '内部部门名称',
  current_node_name VARCHAR(255) NULL COMMENT '当前节点名称',
  current_node_status VARCHAR(100) NULL COMMENT '当前节点状态',
  current_node_started_at DATETIME NULL COMMENT '当前节点进入时间',
  current_node_duration_seconds INT NULL COMMENT '当前节点停留秒数',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (ticket_id, create_dt),
  KEY idx_create_month_label (create_month_label),
  KEY idx_source_updated_at (source_updated_at),
  KEY idx_last_sync_at (last_sync_at),
  KEY idx_ticket_template_id (ticket_template_id),
  KEY idx_ticket_status (ticket_status),
  KEY idx_month_template (create_month_label, ticket_template_id),
  KEY idx_month_status (create_month_label, ticket_status),
  KEY idx_month_region (create_month_label, province, city, district),
  KEY idx_month_problem_type (create_month_label, problem_type),
  KEY idx_month_department (create_month_label, department_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='工单详情主表' {PARTITION_CLAUSE_2025_2026}
"""


TICKET_DETAIL_CUSTOM_FIELDS_DDL = f"""
CREATE TABLE IF NOT EXISTS ticket_detail_custom_fields (
  id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  ticket_id BIGINT NOT NULL COMMENT '工单ID',
  ticket_template_id VARCHAR(255) NULL COMMENT '工单模板',
  create_dt DATETIME NOT NULL COMMENT '工单创建时间（分区键，来自主表）',
  create_year SMALLINT NULL COMMENT '创建年份',
  create_month TINYINT NULL COMMENT '创建月份',
  create_month_label VARCHAR(7) NULL COMMENT '创建年月 YYYY-MM',
  field_order INT NOT NULL COMMENT '字段顺序',
  field_key VARCHAR(255) NOT NULL COMMENT '英文字段或原始字段key',
  field_name VARCHAR(255) NULL COMMENT '中文字段名',
  field_value MEDIUMTEXT NULL COMMENT '字段值文本',
  field_value_json JSON NULL COMMENT '字段值JSON，数组或对象时使用',
  field_value_type VARCHAR(50) NULL COMMENT '字段值类型',
  last_sync_at TIMESTAMP NULL COMMENT '最近同步时间',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id, create_dt),
  UNIQUE KEY uk_ticket_field_order (ticket_id, field_order, create_dt),
  KEY idx_ticket_id (ticket_id),
  KEY idx_month_field (create_month_label, field_name),
  KEY idx_month_template_field (create_month_label, ticket_template_id, field_name),
  KEY idx_field_name (field_name),
  KEY idx_field_key (field_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='工单自定义字段明细表' {PARTITION_CLAUSE_2025_2026}
"""


CUSTOMERS_DDL = """
CREATE TABLE IF NOT EXISTS customers (
  customer_id VARCHAR(255) NOT NULL COMMENT '客户/公司ID',
  customer_name VARCHAR(500) NULL COMMENT '客户/公司名称',
  customer_type VARCHAR(100) NULL COMMENT '客户性质/类型',
  province VARCHAR(50) NULL COMMENT '省份',
  city VARCHAR(50) NULL COMMENT '城市',
  district VARCHAR(50) NULL COMMENT '区县',
  address VARCHAR(500) NULL COMMENT '地址',
  source_flags VARCHAR(100) NULL COMMENT '来源标记 customer,company',
  source_updated_at DATETIME NULL COMMENT '来源更新时间',
  last_sync_at TIMESTAMP NULL COMMENT '最近同步时间',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (customer_id),
  KEY idx_customer_name (customer_name),
  KEY idx_customer_type (customer_type),
  KEY idx_region (province, city, district),
  KEY idx_source_flags (source_flags)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='客户/公司表'
"""


CONTACTS_DDL = """
CREATE TABLE IF NOT EXISTS contacts (
  contact_id VARCHAR(255) NOT NULL COMMENT '联系人ID',
  contact_name VARCHAR(255) NULL COMMENT '联系人姓名',
  phone VARCHAR(100) NULL COMMENT '手机号',
  email VARCHAR(255) NULL COMMENT '邮箱',
  qq VARCHAR(50) NULL COMMENT 'QQ',
  wechat VARCHAR(100) NULL COMMENT '微信',
  customer_id VARCHAR(255) NULL COMMENT '所属客户/公司ID',
  customer_name VARCHAR(500) NULL COMMENT '所属客户/公司名称',
  department_name VARCHAR(255) NULL COMMENT '联系人部门',
  position_name VARCHAR(255) NULL COMMENT '联系人职位',
  source_flags VARCHAR(100) NULL COMMENT '来源标记 contact,company_contact',
  source_updated_at DATETIME NULL COMMENT '来源更新时间',
  last_sync_at TIMESTAMP NULL COMMENT '最近同步时间',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (contact_id),
  KEY idx_customer_id (customer_id),
  KEY idx_contact_name (contact_name),
  KEY idx_phone (phone),
  KEY idx_source_flags (source_flags)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='联系人表'
"""


CUSTOMERS_ALTER_STATEMENTS = (
    "ALTER TABLE customers ADD COLUMN `contact_name` VARCHAR(255) NULL COMMENT '主联系人姓名'",
    "ALTER TABLE customers ADD COLUMN `phone` VARCHAR(100) NULL COMMENT '主联系人电话'",
    "ALTER TABLE customers ADD COLUMN `email` VARCHAR(255) NULL COMMENT '主联系人邮箱'",
    "ALTER TABLE customers ADD COLUMN `row_hash` CHAR(64) NULL COMMENT '业务字段哈希'",
    "ALTER TABLE customers ADD COLUMN `sync_batch_id` CHAR(36) NULL COMMENT '最近同步批次'",
)

CONTACTS_ALTER_STATEMENTS = (
    "ALTER TABLE contacts ADD COLUMN `fixed_phone` VARCHAR(100) NULL COMMENT '固定电话'",
    "ALTER TABLE contacts ADD COLUMN `row_hash` CHAR(64) NULL COMMENT '业务字段哈希'",
    "ALTER TABLE contacts ADD COLUMN `sync_batch_id` CHAR(36) NULL COMMENT '最近同步批次'",
)

CUSTOMER_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS customer_history (
  customer_id VARCHAR(255) NOT NULL,
  version_no INT NOT NULL,
  customer_name VARCHAR(500) NULL,
  customer_type VARCHAR(100) NULL,
  province VARCHAR(50) NULL,
  city VARCHAR(50) NULL,
  district VARCHAR(50) NULL,
  address VARCHAR(500) NULL,
  contact_name VARCHAR(255) NULL,
  phone VARCHAR(100) NULL,
  email VARCHAR(255) NULL,
  source_flags VARCHAR(100) NULL,
  source_updated_at DATETIME NULL,
  row_hash CHAR(64) NOT NULL,
  sync_batch_id CHAR(36) NOT NULL,
  effective_from DATETIME NOT NULL,
  effective_to DATETIME NULL,
  is_current TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (customer_id, version_no),
  KEY idx_customer_history_active (customer_id, is_current, effective_from),
  KEY idx_customer_history_period (effective_from, effective_to)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='客户历史快照表'
"""

CONTACT_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS contact_history (
  contact_id VARCHAR(255) NOT NULL,
  version_no INT NOT NULL,
  contact_name VARCHAR(255) NULL,
  phone VARCHAR(100) NULL,
  fixed_phone VARCHAR(100) NULL,
  email VARCHAR(255) NULL,
  qq VARCHAR(50) NULL,
  wechat VARCHAR(100) NULL,
  customer_id VARCHAR(255) NULL,
  customer_name VARCHAR(500) NULL,
  department_name VARCHAR(255) NULL,
  position_name VARCHAR(255) NULL,
  source_flags VARCHAR(100) NULL,
  source_updated_at DATETIME NULL,
  row_hash CHAR(64) NOT NULL,
  sync_batch_id CHAR(36) NOT NULL,
  effective_from DATETIME NOT NULL,
  effective_to DATETIME NULL,
  is_current TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (contact_id, version_no),
  KEY idx_contact_history_active (contact_id, is_current, effective_from),
  KEY idx_contact_history_period (effective_from, effective_to)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='联系人历史快照表'
"""

CUSTOMER_CONTACT_RELATION_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS customer_contact_relation_history (
  contact_id VARCHAR(255) NOT NULL,
  version_no INT NOT NULL,
  customer_id VARCHAR(255) NULL,
  customer_name VARCHAR(500) NULL,
  sync_batch_id CHAR(36) NOT NULL,
  effective_from DATETIME NOT NULL,
  effective_to DATETIME NULL,
  is_current TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (contact_id, version_no),
  KEY idx_relation_customer_active (customer_id, is_current),
  KEY idx_relation_period (effective_from, effective_to)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='联系人客户归属历史表'
"""

API_SYNC_BATCH_DDL = """
CREATE TABLE IF NOT EXISTS api_sync_batch (
  sync_batch_id CHAR(36) NOT NULL,
  entity_type VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL,
  fetched_count INT NOT NULL DEFAULT 0,
  raw_saved_count INT NOT NULL DEFAULT 0,
  inserted_count INT NOT NULL DEFAULT 0,
  changed_count INT NOT NULL DEFAULT 0,
  unchanged_count INT NOT NULL DEFAULT 0,
  failed_count INT NOT NULL DEFAULT 0,
  error_message TEXT NULL,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  PRIMARY KEY (sync_batch_id),
  KEY idx_api_sync_batch_entity_status (entity_type, status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='客户联系人API同步批次'
"""

API_RAW_RECORD_DDL = """
CREATE TABLE IF NOT EXISTS api_raw_record (
  id BIGINT NOT NULL AUTO_INCREMENT,
  sync_batch_id CHAR(36) NOT NULL,
  entity_type VARCHAR(20) NOT NULL,
  source_name VARCHAR(100) NOT NULL,
  source_record_id VARCHAR(255) NOT NULL,
  payload_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_api_raw_record (sync_batch_id, entity_type, source_name, source_record_id),
  KEY idx_api_raw_entity (entity_type, source_record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='客户联系人API原始留档'
"""

CUSTOMER_SERVICE_VIEW_SQL = """
CREATE OR REPLACE VIEW v_customer_service_overview AS
SELECT
  h.customer_id,
  h.customer_name,
  h.customer_type,
  h.province,
  h.city,
  h.effective_from,
  h.effective_to,
  COUNT(t.ticket_id) AS ticket_count,
  SUM(t.ticket_status IN ('4', '5', '6')) AS resolved_ticket_count,
  AVG(CASE WHEN t.solve_dt IS NOT NULL THEN TIMESTAMPDIFF(SECOND, t.create_dt, t.solve_dt) END) AS avg_resolution_seconds
FROM customer_history h
LEFT JOIN ticket_detail_main t
  ON t.company_id = h.customer_id
  AND t.create_dt >= h.effective_from
  AND (h.effective_to IS NULL OR t.create_dt < h.effective_to)
GROUP BY h.customer_id, h.version_no, h.customer_name, h.customer_type, h.province, h.city, h.effective_from, h.effective_to
"""

CONTACT_SERVICE_VIEW_SQL = """
CREATE OR REPLACE VIEW v_contact_service_overview AS
SELECT
  h.contact_id,
  h.contact_name,
  h.customer_id,
  h.customer_name,
  h.department_name,
  h.position_name,
  h.effective_from,
  h.effective_to,
  COUNT(t.ticket_id) AS ticket_count,
  SUM(t.ticket_status IN ('4', '5', '6')) AS resolved_ticket_count
FROM contact_history h
LEFT JOIN ticket_detail_main t
  ON t.cust_user_id = h.contact_id
  AND t.create_dt >= h.effective_from
  AND (h.effective_to IS NULL OR t.create_dt < h.effective_to)
GROUP BY h.contact_id, h.version_no, h.contact_name, h.customer_id, h.customer_name, h.department_name, h.position_name, h.effective_from, h.effective_to
"""

CUSTOMER_DATA_QUALITY_VIEW_SQL = """
CREATE OR REPLACE VIEW v_customer_data_quality AS
SELECT
  (SELECT COUNT(*) FROM customers) AS customer_count,
  (SELECT COUNT(*) FROM contacts) AS contact_count,
  (SELECT COUNT(*) FROM contacts WHERE customer_id IS NOT NULL AND customer_id <> '') AS linked_contact_count,
  (SELECT COUNT(*) FROM contacts WHERE phone IS NOT NULL AND phone <> '') AS phone_covered_contact_count,
  (SELECT COUNT(*) FROM contacts WHERE email IS NOT NULL AND email <> '') AS email_covered_contact_count,
  (SELECT COUNT(*) FROM ticket_detail_main WHERE company_id IS NULL OR company_id = '') AS unlinked_ticket_count
"""


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


def ensure_mysql_schema(config: MySQLConfig) -> None:
    """创建数据库和 5 张表（含 2 张分区表）。"""

    pymysql = _pymysql()
    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        charset="utf8mb4",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{config.database}` "
                "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
            )
            cursor.execute(f"USE `{config.database}`")
            cursor.execute(TICKET_DETAIL_MAIN_DDL)
            cursor.execute(TICKET_DETAIL_CUSTOM_FIELDS_DDL)
            cursor.execute(CUSTOMERS_DDL)
            cursor.execute(CONTACTS_DDL)
            cursor.execute(SYNC_TASK_LOG_DDL)
            _ensure_ticket_detail_main_columns(cursor, config.database)
            _ensure_customer_contact_analytics_schema(cursor, config.database)


def _ensure_customer_contact_analytics_schema(cursor: Any, database: str) -> None:
    """Create append-only customer/contact analytics tables and missing current columns."""

    cursor.execute(CUSTOMER_HISTORY_DDL)
    cursor.execute(CONTACT_HISTORY_DDL)
    cursor.execute(CUSTOMER_CONTACT_RELATION_HISTORY_DDL)
    cursor.execute(API_SYNC_BATCH_DDL)
    cursor.execute(API_RAW_RECORD_DDL)
    _add_missing_columns(cursor, database, "customers", CUSTOMERS_ALTER_STATEMENTS)
    _add_missing_columns(cursor, database, "contacts", CONTACTS_ALTER_STATEMENTS)


def create_customer_contact_analysis_views(config: MySQLConfig) -> None:
    """Create the query-only customer/contact analytics views."""

    ensure_mysql_schema(config)
    pymysql = _pymysql()
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
            cursor.execute(CUSTOMER_SERVICE_VIEW_SQL)
            cursor.execute(CONTACT_SERVICE_VIEW_SQL)
            cursor.execute(CUSTOMER_DATA_QUALITY_VIEW_SQL)


def _add_missing_columns(cursor: Any, database: str, table_name: str, statements: Iterable[str]) -> None:
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (database, table_name),
    )
    existing = {str(row[0]) for row in cursor.fetchall()}
    for statement in statements:
        match = re.search(r"ADD COLUMN `([^`]+)`", statement)
        if match and match.group(1) not in existing:
            cursor.execute(statement)
            existing.add(match.group(1))


def _ensure_ticket_detail_main_columns(cursor: Any, database: str) -> None:
    """Add columns introduced after the initial schema to existing tables."""

    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'ticket_detail_main'",
        (database,),
    )
    existing = {str(row[0]) for row in cursor.fetchall()}
    if "ticket_category" not in existing:
        cursor.execute(
            "ALTER TABLE ticket_detail_main "
            "ADD COLUMN ticket_category VARCHAR(50) NULL COMMENT '工单类别' AFTER region_text"
        )


def drop_mysql_tables(config: MySQLConfig) -> None:
    """删除全部 5 张表（危险：数据全部丢失）。"""

    pymysql = _pymysql()
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
            for table in (
                "ticket_detail_custom_fields",
                "ticket_detail_main",
                "customers",
                "contacts",
                "sync_task_log",
            ):
                cursor.execute(f"DROP TABLE IF EXISTS `{table}`")


# ---------------------------------------------------------------------------
# 分区管理
# ---------------------------------------------------------------------------

def get_existing_partitions(config: MySQLConfig) -> set[str]:
    """返回工单主表中当前已存在的分区名称集合。"""

    pymysql = _pymysql()
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
                "SELECT PARTITION_NAME FROM INFORMATION_SCHEMA.PARTITIONS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'ticket_detail_main' "
                "AND PARTITION_NAME IS NOT NULL",
                (config.database,),
            )
            return {row[0] for row in cursor.fetchall()}


def add_future_partitions(config: MySQLConfig, months: list[tuple[int, int]]) -> list[str]:
    """为工单表增加新的月度分区。

    通过 REORGANIZE PARTITION pmax 把兜底分区拆分为一个具体月分区 + 新的 pmax。
    对已存在的月份自动跳过。返回实际新建的分区名列表。
    """

    existing = get_existing_partitions(config)
    created: list[str] = []

    pymysql = _pymysql()
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
            for year, month in months:
                partition_name = f"p{year}{month:02d}"
                if partition_name in existing:
                    continue
                next_month = month + 1
                next_year = year
                if next_month > 12:
                    next_month = 1
                    next_year += 1
                upper_bound = f"{next_year}-{next_month:02d}-01"
                for table in ("ticket_detail_main", "ticket_detail_custom_fields"):
                    try:
                        cursor.execute(
                            f"ALTER TABLE `{table}` REORGANIZE PARTITION pmax INTO ("
                            f"PARTITION `{partition_name}` VALUES LESS THAN ('{upper_bound}'), "
                            f"PARTITION pmax VALUES LESS THAN (MAXVALUE))"
                        )
                    except Exception:
                        # 分区可能已存在（并发或重复调用），忽略
                        pass
                existing.add(partition_name)
                created.append(partition_name)
    return created


def generate_months_ahead(months_count: int) -> list[tuple[int, int]]:
    """生成从当前月份起未来 N 个月的 (year, month) 列表。"""

    now = datetime.now()
    year, month = now.year, now.month
    result: list[tuple[int, int]] = []
    for _ in range(months_count):
        result.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return result


# ---------------------------------------------------------------------------
# 工单导入
# ---------------------------------------------------------------------------

def _fetch_month_ticket_rows(
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int,
    limit_per_month: int | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Fetch monthly ticket list directly from the API."""

    from .monthly_export import build_month_label, fetch_month_ticket_rows

    month_label = build_month_label(year, month)
    ticket_report = fetch_month_ticket_rows(
        client,
        year,
        month,
        per_page=per_page,
        limit_per_month=limit_per_month,
    )
    return month_label, ticket_report.get("tickets", []), "api"


def import_ticket_detail_to_mysql(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    ticket_id: str,
) -> dict[str, Any]:
    """拉取单条工单详情，完成 value 替换后写入 MySQL。"""

    ensure_mysql_schema(config)
    raw_detail = client.fetch_ticket_detail(ticket_id)
    if not raw_detail:
        raise ApiError(f"Ticket detail not found: {ticket_id}")
    field_resolver = TicketFieldResolver(client.fetch_ticket_fields(), client.fetch_company_fields())
    value_detail = resolve_ticket_detail_values(raw_detail, client, field_resolver)
    main_row = build_ticket_detail_main_row(value_detail)
    custom_rows = build_ticket_detail_custom_field_rows(raw_detail, value_detail)
    upsert_ticket_detail(config, main_row, custom_rows)
    return {
        "ticket_id": main_row["ticket_id"],
        "main_rows": 1,
        "custom_field_rows": len(custom_rows),
    }


def import_month_tickets_serial(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """拉取某个月的全部工单详情，逐条串行导入（调试用，速度较慢）。"""

    month_label, ticket_rows, ticket_source = _fetch_month_ticket_rows(
        client,
        year,
        month,
        per_page=per_page,
        limit_per_month=limit_per_month,
    )
    if not ticket_rows:
        return {
            "month": month_label,
            "total_in_month": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "failed_ids": [],
        }

    ensure_mysql_schema(config)
    ticket_ids, already_current = _filter_ticket_rows_for_import(config, ticket_rows, month_label)
    if not ticket_ids:
        _write_sync_log(
            config,
            task_type="ticket_detail",
            target_year=year,
            target_month=month,
            month_label=month_label,
            status="success",
            total_count=len(ticket_rows),
            success_count=0,
            failed_count=0,
            skipped_count=already_current,
            duration_seconds=0,
            error_message=None,
            extra_json={"ticket_source": ticket_source, "prefiltered": True},
        )
        return {
            "month": month_label,
            "ticket_source": ticket_source,
            "total_in_month": len(ticket_rows),
            "imported": 0,
            "updated": 0,
            "skipped": already_current,
            "failed": 0,
            "failed_ids": [],
            "custom_field_rows": 0,
            "duration_seconds": 0,
        }

    field_resolver = TicketFieldResolver(client.fetch_ticket_fields(), client.fetch_company_fields())

    imported = 0
    updated = 0
    skipped = already_current
    failed_ids: list[str] = []
    total_custom = 0
    started_at = datetime.now()

    for ticket_id in ticket_ids:
        try:
            raw_detail = client.fetch_ticket_detail(ticket_id)
            if not raw_detail:
                failed_ids.append(ticket_id)
                continue
            value_detail = resolve_ticket_detail_values(raw_detail, client, field_resolver)
            detail_map = {ticket_id: (raw_detail, value_detail)}
            batch_result = _commit_batch_atomic(config, detail_map)
            imported += batch_result["imported"]
            updated += batch_result["updated"]
            skipped += batch_result["skipped"]
            failed_ids.extend(batch_result["failed_ids"])
            total_custom += batch_result["custom_rows"]
        except Exception:
            failed_ids.append(ticket_id)

    duration = int((datetime.now() - started_at).total_seconds())
    overall_status = "success" if not failed_ids else ("partial" if (imported + updated) > 0 else "failed")
    _write_sync_log(
        config, task_type="ticket_detail",
        target_year=year, target_month=month, month_label=month_label,
        status=overall_status, total_count=len(ticket_ids),
        success_count=imported + updated, failed_count=len(failed_ids),
        skipped_count=skipped, duration_seconds=duration,
        error_message=None if overall_status == "success" else f"{len(failed_ids)} 条工单失败",
        extra_json={"failed_ids": failed_ids} if failed_ids else None,
    )

    return {
        "month": month_label,
        "ticket_source": ticket_source,
        "total_in_month": len(ticket_ids),
        "imported": imported, "updated": updated, "skipped": skipped,
        "failed": len(failed_ids), "failed_ids": failed_ids,
        "custom_field_rows": total_custom, "duration_seconds": duration,
    }


def import_month_tickets_to_mysql(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    max_workers: int = 8,
    batch_size: int = 100,
    api_rate_limit: int = 10,
) -> dict[str, Any]:
    """Import one month of tickets directly from the API.

    优化策略：
    - 全月工单列表中的实体 ID 去重后预取，后续逐条解析直接命中缓存；
    - 信号量跨批次共享，统一控制 API QPS；
    - 每批用独立连接 + 独立事务，单批失败不影响其他批次。
    """

    month_label, ticket_rows, ticket_source = _fetch_month_ticket_rows(
        client,
        year,
        month,
        per_page=per_page,
        limit_per_month=limit_per_month,
    )
    if not ticket_rows:
        return {
            "month": month_label,
            "ticket_source": ticket_source,
            "total_in_month": 0,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "failed_ids": [],
        }

    ensure_mysql_schema(config)
    ticket_ids, already_current = _filter_ticket_rows_for_import(config, ticket_rows, month_label)
    if not ticket_ids:
        _write_sync_log(
            config,
            task_type="ticket_detail",
            target_year=year,
            target_month=month,
            month_label=month_label,
            status="success",
            total_count=len(ticket_rows),
            success_count=0,
            failed_count=0,
            skipped_count=already_current,
            duration_seconds=0,
            error_message=None,
            extra_json={
                "ticket_source": ticket_source,
                "limit_per_month": limit_per_month,
                "prefiltered": True,
            },
        )
        return {
            "month": month_label,
            "ticket_source": ticket_source,
            "total_in_month": len(ticket_rows),
            "imported": 0,
            "updated": 0,
            "skipped": already_current,
            "failed": 0,
            "failed_ids": [],
            "custom_field_rows": 0,
            "duration_seconds": 0,
        }

    field_resolver = TicketFieldResolver(client.fetch_ticket_fields(), client.fetch_company_fields())
    pending_ids = set(ticket_ids)
    pending_rows = [
        row
        for row in ticket_rows
        if str(row.get("ticketId") or "").strip() in pending_ids
    ]

    # ── 1. 预取实体详情（去重后并发请求）────────────────────────
    _prefetch_ticket_entities(client, pending_rows, field_resolver, max_workers, api_rate_limit)

    # ── 2. 分批次获取详情 + 入库 ─────────────────────────────────
    imported = 0
    updated = 0
    skipped = already_current
    failed_ids: list[str] = []
    total_custom = 0
    started_at = datetime.now()
    api_semaphore = threading.Semaphore(max(1, api_rate_limit))

    for batch_start in range(0, len(ticket_ids), batch_size):
        batch = ticket_ids[batch_start:batch_start + batch_size]
        detail_map = _fetch_batch_details(client, batch, field_resolver, api_semaphore, max_workers=max_workers)
        missing_detail_ids = [tid for tid in batch if tid not in detail_map]
        batch_result = _commit_batch_atomic(config, detail_map)
        imported += batch_result["imported"]
        updated += batch_result["updated"]
        skipped += batch_result["skipped"]
        failed_ids.extend(missing_detail_ids)
        failed_ids.extend(batch_result["failed_ids"])
        total_custom += batch_result["custom_rows"]

    duration = int((datetime.now() - started_at).total_seconds())
    overall_status = "success" if not failed_ids else ("partial" if (imported + updated) > 0 else "failed")
    _write_sync_log(
        config,
        task_type="ticket_detail",
        target_year=year,
        target_month=month,
        month_label=month_label,
        status=overall_status,
        total_count=len(ticket_rows),
        success_count=imported + updated,
        failed_count=len(failed_ids),
        skipped_count=skipped,
        duration_seconds=duration,
        error_message=None if overall_status == "success" else f"{len(failed_ids)} tickets failed",
        extra_json={
            "failed_ids": failed_ids,
            "ticket_source": ticket_source,
            "limit_per_month": limit_per_month,
        } if failed_ids else {
            "ticket_source": ticket_source,
            "limit_per_month": limit_per_month,
        },
    )

    return {
        "month": month_label,
        "ticket_source": ticket_source,
        "total_in_month": len(ticket_rows),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed": len(failed_ids),
        "failed_ids": failed_ids,
        "custom_field_rows": total_custom,
        "duration_seconds": duration,
    }


def _prefetch_ticket_entities(
    client: WorkOrderClient,
    ticket_rows: list[dict[str, Any]],
    field_resolver: TicketFieldResolver,
    max_workers: int,
    api_rate_limit: int,
) -> None:
    """从工单列表中提取所有引用的实体 ID，去重后批量预取详情。

    预取后，后续逐条调用 resolve_ticket_detail_values 时，
    fetch_contact_detail / fetch_company_detail 等几乎全部命中 LRU 缓存。
    """

    contact_ids: set[str] = set()
    support_ids: set[str] = set()
    group_ids: set[str] = set()
    template_ids: set[str] = set()

    for row in ticket_rows:
        if cust := _str_or_none(row.get("custUserId")):
            contact_ids.add(cust)
        for key in ("servicerUserId", "createrId", "deleterId"):
            if sid := _str_or_none(row.get(key)):
                support_ids.add(sid)
        if gid := _str_or_none(row.get("servicerGroupId")):
            group_ids.add(gid)
        if tid := _str_or_none(row.get("ticketTemplateId")):
            template_ids.add(tid)
        # ccUserIdList 里的客服 ID
        for cid_list_field in ("ccUserIdList",):
            for item in _split_id_list(row.get(cid_list_field)):
                support_ids.add(item)
        for gid_list_field in ("ccGroupIdList",):
            for item in _split_id_list(row.get(gid_list_field)):
                group_ids.add(item)

    semaphore = threading.Semaphore(max(1, api_rate_limit))

    client.prefetch_entities(
        contacts=contact_ids,
        companies=set(),  # 公司 ID 在解析联系人后才能确定，无法提前预取
        supports=support_ids,
        groups=group_ids,
        templates=template_ids,
        max_workers=max_workers,
        semaphore=semaphore,
    )


def _str_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text and text != "0" else None


def _filter_ticket_rows_for_import(
    config: MySQLConfig,
    ticket_rows: list[dict[str, Any]],
    month_label: str,
) -> tuple[list[str], int]:
    """Return ticket IDs that need detail fetching, plus count already current.

    The monthly list already contains ticketId/createDT/updateDT. Use that to
    avoid refetching details for rows whose source_updated_at is unchanged.
    """

    candidates: list[tuple[str, datetime | None, datetime | None]] = []
    for row in ticket_rows:
        ticket_id = str(row.get("ticketId") or "").strip()
        if not ticket_id:
            continue
        candidates.append((ticket_id, _to_datetime(row.get("createDT")), _to_datetime(row.get("updateDT"))))

    if not candidates:
        return [], 0

    existing: dict[str, datetime | None] = {}
    pymysql = _pymysql()
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
            ids = [ticket_id for ticket_id, _create_dt, _update_dt in candidates]
            for start in range(0, len(ids), 1000):
                chunk = ids[start:start + 1000]
                placeholders = ", ".join(["%s"] * len(chunk))
                cursor.execute(
                    "SELECT ticket_id, source_updated_at "
                    "FROM ticket_detail_main "
                    f"WHERE create_month_label = %s AND ticket_id IN ({placeholders})",
                    [month_label, *chunk],
                )
                for ticket_id, source_updated_at in cursor.fetchall():
                    existing[str(ticket_id)] = source_updated_at

    pending: list[str] = []
    skipped = 0
    for ticket_id, _create_dt, update_dt in candidates:
        if update_dt is None:
            pending.append(ticket_id)
            continue
        source_updated = existing.get(ticket_id)
        if source_updated is not None and _same_datetime(source_updated, update_dt):
            skipped += 1
        else:
            pending.append(ticket_id)
    return pending, skipped


def _same_datetime(left: datetime, right: datetime) -> bool:
    """Compare datetimes at second precision; MySQL may drop microseconds."""

    return left.replace(microsecond=0) == right.replace(microsecond=0)


def _commit_batch_atomic(
    config: MySQLConfig,
    detail_map: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Commit one batch in a single connection + single transaction.

    The batch already has row-by-row fallback inside _commit_batch,
    so no outer retry is needed.
    """

    if not detail_map:
        return {
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed_ids": [],
            "custom_rows": 0,
        }

    pymysql = _pymysql()
    try:
        with pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
            charset="utf8mb4",
            autocommit=False,
        ) as connection:
            return _commit_batch(connection, detail_map)
    except Exception:
        return {
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "failed_ids": [str(tid) for tid in detail_map.keys()],
            "custom_rows": 0,
        }


def _fetch_batch_details(
    client: WorkOrderClient,
    ticket_ids: list[str],
    field_resolver: TicketFieldResolver,
    semaphore: threading.Semaphore,
    max_workers: int = 8,
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Fetch raw details and resolved details together so raw field keys are preserved."""

    def _fetch_one(ticket_id: str) -> tuple[str, tuple[dict[str, Any], dict[str, Any]] | None]:
        with semaphore:
            try:
                raw = client.fetch_ticket_detail(ticket_id)
                if not raw:
                    return ticket_id, None
                value = resolve_ticket_detail_values(raw, client, field_resolver)
                return ticket_id, (raw, value)
            except Exception:
                return ticket_id, None

    results: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    worker_count = max(1, min(max_workers, len(ticket_ids))) if ticket_ids else 1
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_fetch_one, ticket_id): ticket_id for ticket_id in ticket_ids}
        for future in as_completed(futures):
            ticket_id, detail_pair = future.result()
            if detail_pair is not None:
                results[ticket_id] = detail_pair
    return results


def _commit_batch(
    connection: Any,
    detail_map: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """Write parsed ticket details in one transaction, retrying row-by-row on batch failure."""

    imported = 0
    updated = 0
    skipped = 0
    failed_ids: list[str] = []
    custom_rows_total = 0

    batch_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for _ticket_id, (raw_detail, value_detail) in detail_map.items():
        main_row = build_ticket_detail_main_row(value_detail)
        custom_rows = build_ticket_detail_custom_field_rows(raw_detail, value_detail)
        batch_rows.append((main_row, custom_rows))

    try:
        with connection.cursor() as cursor:
            for main_row, custom_rows in batch_rows:
                action = _upsert_ticket_detail(cursor, main_row, custom_rows)
                if action == "updated":
                    updated += 1
                    custom_rows_total += len(custom_rows)
                elif action == "skipped":
                    skipped += 1
                else:
                    imported += 1
                    custom_rows_total += len(custom_rows)
        connection.commit()
    except Exception:
        _safe_rollback(connection)
        for main_row, custom_rows in batch_rows:
            try:
                with connection.cursor() as cursor:
                    action = _upsert_ticket_detail(cursor, main_row, custom_rows)
                connection.commit()
                if action == "updated":
                    updated += 1
                    custom_rows_total += len(custom_rows)
                elif action == "skipped":
                    skipped += 1
                else:
                    imported += 1
                    custom_rows_total += len(custom_rows)
            except Exception:
                failed_ids.append(str(main_row.get("ticket_id", "")))
                _safe_rollback(connection)

    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "failed_ids": failed_ids,
        "custom_rows": custom_rows_total,
    }


def _safe_rollback(connection: Any) -> None:
    """Rollback if the connection is still usable."""

    try:
        connection.rollback()
    except Exception:
        pass


def import_year_tickets_to_mysql(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    months: Iterable[int] | None = None,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    max_workers: int = 8,
    batch_size: int = 100,
    api_rate_limit: int = 10,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """拉取某年指定月份的全部工单详情并写入 MySQL。

    默认导入全年 12 个月。支持断点续跑：已导入的月份可跳过。
    """

    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    month_numbers = list(months) if months is not None else list(range(1, 13))
    month_reports: list[dict[str, Any]] = []
    total_imported = 0
    total_updated = 0
    total_skipped = 0
    total_failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task(f"MySQL 导入 {year}", total=len(month_numbers))
        for month in month_numbers:
            from .monthly_export import build_month_label
            month_label = build_month_label(year, month)
            progress.update(task, description=f"导入 {month_label}")
            report = import_month_tickets_to_mysql(
                config, dictionary, client, year, month,
                per_page=per_page, limit_per_month=limit_per_month,
                max_workers=max_workers,
                batch_size=batch_size, api_rate_limit=api_rate_limit,
            )
            month_reports.append(report)
            total_imported += report["imported"]
            total_updated += report["updated"]
            total_skipped += report["skipped"]
            total_failed += report["failed"]
            progress.advance(task)

    return {
        "year": year,
        "total_imported": total_imported,
        "total_updated": total_updated,
        "total_skipped": total_skipped,
        "total_failed": total_failed,
        "months": month_reports,
    }


# ---------------------------------------------------------------------------
# 客户 / 联系人导入
# ---------------------------------------------------------------------------

def import_customers_to_mysql(
    config: MySQLConfig,
    client: WorkOrderClient,
    sources: Iterable[str] = ("companies",),
    require_nonempty: bool = True,
    max_records: int | None = None,
) -> dict[str, Any]:
    """拉取客户/公司列表，upsert 到 customers 表。"""

    from .customer_contact_sync import sync_customer_entities

    report = sync_customer_entities(
        config, client, sources=sources, require_nonempty=require_nonempty, max_records=max_records,
    )
    return {
        "total": report.fetched,
        "succeeded": report.inserted + report.changed + report.unchanged,
        "failed": report.failed,
        "duration_seconds": 0,
        "batch_id": report.batch_id,
        "status": report.status,
        "inserted": report.inserted,
        "changed": report.changed,
        "unchanged": report.unchanged,
        "raw_saved": report.raw_saved,
    }

    ensure_mysql_schema(config)
    started_at = datetime.now()
    total = 0
    succeeded = 0
    failed = 0
    seen_ids: set[str] = set()

    rows: list[dict[str, Any]] = []
    for source in sources:
        if source == "companies":
            fetched = client.fetch_companies()
        elif source == "customers":
            fetched = client.fetch_customers()
        else:
            continue
        for item in fetched:
            cid = str(item.get("companyId") or item.get("customerId") or item.get("id") or "").strip()
            if not cid or cid in seen_ids:
                continue
            seen_ids.add(cid)
            rows.append({
                "customer_id": cid,
                "customer_name": _text_or_none(item.get("companyName") or item.get("customerName") or item.get("name")),
                "customer_type": _text_or_none(item.get("customerType") or item.get("type")),
                "province": _text_or_none(item.get("province") or item.get("area")),
                "city": _text_or_none(item.get("city")),
                "district": _text_or_none(item.get("district")),
                "address": _text_or_none(item.get("address")),
                "source_flags": source,
            })

    pymysql = _pymysql()
    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    ) as connection:
        with connection.cursor() as cursor:
            for row in rows:
                try:
                    columns = list(row.keys())
                    placeholders = ", ".join(["%s"] * len(columns))
                    column_sql = ", ".join(f"`{c}`" for c in columns)
                    update_sql = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in columns if c != "customer_id")
                    values = [row[c] for c in columns]
                    cursor.execute(
                        f"INSERT INTO customers ({column_sql}) VALUES ({placeholders}) "
                        f"ON DUPLICATE KEY UPDATE {update_sql}, updated_at = CURRENT_TIMESTAMP",
                        values,
                    )
                    succeeded += 1
                except Exception:
                    failed += 1
            connection.commit()

    total = len(rows)
    duration = int((datetime.now() - started_at).total_seconds())
    _write_sync_log(
        config,
        task_type="customer",
        month_label="-",
        status="success" if failed == 0 else "partial",
        total_count=total,
        success_count=succeeded,
        failed_count=failed,
        duration_seconds=duration,
    )
    return {"total": total, "succeeded": succeeded, "failed": failed, "duration_seconds": duration}


def import_contacts_to_mysql(
    config: MySQLConfig,
    client: WorkOrderClient,
    sources: Iterable[str] = ("contacts",),
    require_nonempty: bool = True,
    max_records: int | None = None,
) -> dict[str, Any]:
    """拉取联系人列表，upsert 到 contacts 表。"""

    from .customer_contact_sync import sync_contact_entities

    report = sync_contact_entities(
        config, client, sources=sources, require_nonempty=require_nonempty, max_records=max_records,
    )
    return {
        "total": report.fetched,
        "succeeded": report.inserted + report.changed + report.unchanged,
        "failed": report.failed,
        "duration_seconds": 0,
        "batch_id": report.batch_id,
        "status": report.status,
        "inserted": report.inserted,
        "changed": report.changed,
        "unchanged": report.unchanged,
        "raw_saved": report.raw_saved,
    }

    ensure_mysql_schema(config)
    started_at = datetime.now()
    total = 0
    succeeded = 0
    failed = 0
    seen_ids: set[str] = set()

    rows: list[dict[str, Any]] = []
    for source in sources:
        if source == "contacts":
            fetched = client.fetch_contacts()
        elif source == "company_contacts":
            fetched = client.fetch_company_contacts()
        else:
            continue
        for item in fetched:
            cid = str(item.get("contactId") or item.get("cId") or item.get("userId") or item.get("id") or "").strip()
            if not cid or cid in seen_ids:
                continue
            seen_ids.add(cid)
            rows.append({
                "contact_id": cid,
                "contact_name": _text_or_none(item.get("contactName") or item.get("realName") or item.get("name")),
                "phone": _text_or_none(item.get("phone") or item.get("mobile") or item.get("fixnumber")),
                "email": _text_or_none(item.get("email")),
                "qq": _text_or_none(item.get("qq")),
                "wechat": _text_or_none(item.get("wechat")),
                "customer_id": _text_or_none(item.get("companyId") or item.get("customerId")),
                "customer_name": _text_or_none(item.get("companyName") or item.get("customerName")),
                "department_name": _text_or_none(item.get("departmentName") or item.get("department")),
                "position_name": _text_or_none(item.get("positionName") or item.get("position")),
                "source_flags": source,
            })

    pymysql = _pymysql()
    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    ) as connection:
        with connection.cursor() as cursor:
            for row in rows:
                try:
                    columns = list(row.keys())
                    placeholders = ", ".join(["%s"] * len(columns))
                    column_sql = ", ".join(f"`{c}`" for c in columns)
                    update_sql = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in columns if c != "contact_id")
                    values = [row[c] for c in columns]
                    cursor.execute(
                        f"INSERT INTO contacts ({column_sql}) VALUES ({placeholders}) "
                        f"ON DUPLICATE KEY UPDATE {update_sql}, updated_at = CURRENT_TIMESTAMP",
                        values,
                    )
                    succeeded += 1
                except Exception:
                    failed += 1
            connection.commit()

    total = len(rows)
    duration = int((datetime.now() - started_at).total_seconds())
    _write_sync_log(
        config,
        task_type="contact",
        month_label="-",
        status="success" if failed == 0 else "partial",
        total_count=total,
        success_count=succeeded,
        failed_count=failed,
        duration_seconds=duration,
    )
    return {"total": total, "succeeded": succeeded, "failed": failed, "duration_seconds": duration}


# ---------------------------------------------------------------------------
# sync_task_log 写入
# ---------------------------------------------------------------------------

def _write_sync_log(
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
    extra_json: dict | None = None,
) -> None:
    """写入一条同步任务日志。"""

    pymysql = _pymysql()
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


# ---------------------------------------------------------------------------
# 主表 / 自定义字段行构建
# ---------------------------------------------------------------------------

def build_ticket_detail_main_row(value_detail: dict[str, Any]) -> dict[str, Any]:
    """把 value_resolved 工单详情顶层字段转换为主表行。"""

    row: dict[str, Any] = {}
    for api_key, column in MAIN_FIELD_COLUMN_MAP.items():
        value = value_detail.get(api_key)
        if column == "ticket_id":
            row[column] = int(str(value))
        elif column in DATETIME_COLUMNS:
            row[column] = _to_datetime(value)
        elif column in JSON_COLUMNS:
            row[column] = _json_or_none(value)
        else:
            row[column] = _text_or_none(value)

    # 分析维度字段（由 resolver 提供）
    for column in ANALYTIC_COLUMNS:
        raw_val = value_detail.get(column)
        if column in DATETIME_COLUMNS:
            row[column] = _to_datetime(raw_val)
        else:
            row[column] = _text_or_none(raw_val)

    if not row.get("ticket_category"):
        row["ticket_category"] = "原单"

    # create_dt 派生列
    create_dt = row.get("create_dt")
    if create_dt and isinstance(create_dt, datetime):
        row["create_year"] = create_dt.year
        row["create_month"] = create_dt.month
        row["create_month_label"] = create_dt.strftime("%Y-%m")
    else:
        row["create_year"] = None
        row["create_month"] = None
        row["create_month_label"] = None

    row["last_sync_at"] = datetime.now()
    row["sync_status"] = "success"

    return row


def build_ticket_detail_custom_field_rows(
    raw_detail: dict[str, Any],
    value_detail: dict[str, Any],
) -> list[dict[str, Any]]:
    """把 custom_fields 转换为明细表多行。"""

    ticket_id = int(str(raw_detail.get("ticketId") or value_detail.get("ticketId")))
    template_id = _text_or_none(value_detail.get("ticketTemplateId") or raw_detail.get("ticketTemplateId"))
    raw_fields = raw_detail.get("custom_fields") if isinstance(raw_detail.get("custom_fields"), list) else []
    value_fields = value_detail.get("custom_fields") if isinstance(value_detail.get("custom_fields"), list) else []

    # create_dt 用于分区：从 value_detail 的 create_dt 取，需转成 date
    create_dt_raw = value_detail.get("create_dt") or raw_detail.get("createDT")
    create_dt = _to_datetime(create_dt_raw)
    create_year = create_dt.year if create_dt else None
    create_month = create_dt.month if create_dt else None
    create_month_label = create_dt.strftime("%Y-%m") if create_dt else None

    rows: list[dict[str, Any]] = []
    max_len = max(len(raw_fields), len(value_fields))
    for index in range(max_len):
        raw_item = raw_fields[index] if index < len(raw_fields) and isinstance(raw_fields[index], dict) else {}
        value_item = value_fields[index] if index < len(value_fields) and isinstance(value_fields[index], dict) else {}
        field_value = value_item.get("value", raw_item.get("value"))
        rows.append(
            {
                "ticket_id": ticket_id,
                "ticket_template_id": template_id,
                "create_dt": create_dt,
                "create_year": create_year,
                "create_month": create_month,
                "create_month_label": create_month_label,
                "field_order": index + 1,
                "field_key": _text_or_none(raw_item.get("key")) or "",
                "field_name": _text_or_none(value_item.get("key") or raw_item.get("key")),
                "field_value": _text_or_none(field_value),
                "field_value_json": _json_or_none(field_value) if isinstance(field_value, (dict, list)) else None,
                "field_value_type": _value_type(field_value),
                "last_sync_at": datetime.now(),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 写入 / 更新（单事务，按条提交在外层）
# ---------------------------------------------------------------------------

def upsert_ticket_detail(
    config: MySQLConfig,
    main_row: dict[str, Any],
    custom_rows: list[dict[str, Any]],
) -> str:
    """单条工单主表 + 自定义字段明细的事务写入。返回 'inserted' | 'updated'。"""

    pymysql = _pymysql()
    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    ) as connection:
        try:
            with connection.cursor() as cursor:
                action = _upsert_ticket_detail(cursor, main_row, custom_rows)
            connection.commit()
            return action
        except Exception:
            connection.rollback()
            raise


def _upsert_ticket_detail(cursor: Any, main_row: dict[str, Any], custom_rows: list[dict[str, Any]]) -> str:
    """在同一个 cursor 上执行：主表 upsert + 自定义字段全量刷新。

    返回 'updated'（source_updated_at 有变化或主表为更新）/ 'inserted'。
    """

    columns = _main_columns()
    main_ticket_id = main_row.get("ticket_id")
    main_create_dt = main_row.get("create_dt")

    # 1) 查询现有 source_updated_at
    source_updated = _fetch_existing_source_updated(cursor, main_ticket_id, main_create_dt)

    if source_updated is not None and source_updated == main_row.get("source_updated_at"):
        # 未变化：跳过主表更新，仅刷新 sync 标记
        cursor.execute(
            "UPDATE ticket_detail_main SET last_sync_at = NOW(), sync_status = 'skipped' "
            "WHERE ticket_id = %s AND create_dt = %s",
            (main_ticket_id, main_create_dt),
        )
        return "skipped"

    # 2) 主表 upsert
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(f"`{c}`" for c in columns)
    update_sql = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in columns if c not in ("ticket_id", "create_dt"))
    values = [main_row.get(c) for c in columns]
    cursor.execute(
        f"INSERT INTO ticket_detail_main ({column_sql}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_sql}, updated_at = CURRENT_TIMESTAMP",
        values,
    )

    action = "updated"
    if source_updated is None:
        action = "inserted"

    # 3) 自定义字段全量刷新
    cursor.execute(
        "DELETE FROM ticket_detail_custom_fields WHERE ticket_id = %s AND create_dt = %s",
        (main_ticket_id, main_create_dt),
    )
    if custom_rows:
        _insert_custom_rows(cursor, custom_rows)

    return action


def _main_columns() -> list[str]:
    """ticket_detail_main 全部列名（按 insert 顺序）。"""

    base = list(MAIN_FIELD_COLUMN_MAP.values())
    analytic = list(ANALYTIC_COLUMNS)
    derived = ["create_year", "create_month", "create_month_label", "last_sync_at", "sync_status", "sync_error"]
    return base + analytic + derived


def _fetch_existing_source_updated(cursor: Any, ticket_id: Any, create_dt: Any) -> datetime | None:
    """查询当前主表行中的 source_updated_at；不存在返回 None。"""

    if create_dt is None:
        return None
    cursor.execute(
        "SELECT source_updated_at FROM ticket_detail_main WHERE ticket_id = %s AND create_dt = %s",
        (ticket_id, create_dt),
    )
    row = cursor.fetchone()
    if row and row[0]:
        return row[0]
    return None


def _insert_custom_rows(cursor: Any, rows: list[dict[str, Any]]) -> None:
    """批量 insert 自定义字段行（带 ON DUPLICATE KEY UPDATE 以防万一）。"""

    if not rows:
        return
    columns = [
        "ticket_id",
        "ticket_template_id",
        "create_dt",
        "create_year",
        "create_month",
        "create_month_label",
        "field_order",
        "field_key",
        "field_name",
        "field_value",
        "field_value_json",
        "field_value_type",
        "last_sync_at",
    ]
    column_sql = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_sql = ", ".join(
        f"`{c}` = VALUES(`{c}`)"
        for c in columns
        if c not in ("ticket_id", "field_order", "create_dt")
    )
    values = [[row.get(c) for c in columns] for row in rows]
    cursor.executemany(
        f"INSERT INTO ticket_detail_custom_fields ({column_sql}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_sql}, updated_at = CURRENT_TIMESTAMP",
        values,
    )


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _pymysql() -> Any:
    """延迟导入 PyMySQL，便于没有使用 MySQL 功能时仍可运行其它命令。"""

    try:
        import pymysql
    except ImportError as exc:
        raise ApiError("Missing dependency PyMySQL. Run `uv sync` before using MySQL commands.") from exc
    return pymysql


def _to_datetime(value: Any) -> datetime | None:
    """把接口里的时间字符串转成 datetime。"""

    text = str(value or "").strip()
    if not text or text == "0":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _text_or_none(value: Any) -> str | None:
    """普通字段转文本；数组和对象转 JSON 字符串。"""

    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text if text else None


def _json_or_none(value: Any) -> str | None:
    """把 JSON 字段转成 MySQL 可接收的 JSON 字符串。"""

    if value in (None, ""):
        return None
    return json.dumps(value, ensure_ascii=False)


def _value_type(value: Any) -> str:
    """返回字段值类型。"""

    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if value is None:
        return "null"
    return type(value).__name__
