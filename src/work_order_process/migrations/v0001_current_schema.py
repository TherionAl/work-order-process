"""Frozen baseline migration for the original current work-order schema."""

from __future__ import annotations

from typing import Any


VERSION = 1
NAME = "current_schema"

# This payload is intentionally frozen here. Runtime schema constants may evolve
# only through later migration versions; changing this file changes its checksum.
_TABLE_DDLS: tuple[tuple[str, str], ...] = (
    ('ticket_detail_main', """
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
COMMENT='工单详情主表'
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

"""),
    ('ticket_detail_custom_fields', """
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
COMMENT='工单自定义字段明细表'
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

"""),
    ('customers', """
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
"""),
    ('contacts', """
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
"""),
    ('sync_task_log', """
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
"""),
    ('customer_history', """
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
"""),
    ('contact_history', """
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
"""),
    ('customer_contact_relation_history', """
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
"""),
    ('api_sync_batch', """
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
"""),
    ('api_raw_record', """
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
"""),
)

_COMPATIBILITY_ALTERS: dict[str, tuple[tuple[str, str], ...]] = {
    'ticket_detail_main': (
        ('ticket_category', "ALTER TABLE ticket_detail_main ADD COLUMN ticket_category VARCHAR(50) NULL COMMENT '工单类别' AFTER region_text"),
    ),
    'customers': (
        ('contact_name', "ALTER TABLE customers ADD COLUMN `contact_name` VARCHAR(255) NULL COMMENT '主联系人姓名'"),
        ('phone', "ALTER TABLE customers ADD COLUMN `phone` VARCHAR(100) NULL COMMENT '主联系人电话'"),
        ('email', "ALTER TABLE customers ADD COLUMN `email` VARCHAR(255) NULL COMMENT '主联系人邮箱'"),
        ('row_hash', "ALTER TABLE customers ADD COLUMN `row_hash` CHAR(64) NULL COMMENT '业务字段哈希'"),
        ('sync_batch_id', "ALTER TABLE customers ADD COLUMN `sync_batch_id` CHAR(36) NULL COMMENT '最近同步批次'"),
    ),
    'contacts': (
        ('fixed_phone', "ALTER TABLE contacts ADD COLUMN `fixed_phone` VARCHAR(100) NULL COMMENT '固定电话'"),
        ('row_hash', "ALTER TABLE contacts ADD COLUMN `row_hash` CHAR(64) NULL COMMENT '业务字段哈希'"),
        ('sync_batch_id', "ALTER TABLE contacts ADD COLUMN `sync_batch_id` CHAR(36) NULL COMMENT '最近同步批次'"),
    ),
}

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    'ticket_detail_main': frozenset(('ticket_id', 'subject', 'descript', 'cust_user_id', 'cust_user_name', 'company_id', 'company_name', 'servicer_user_id', 'servicer_user_name', 'cc_user_id_list', 'ticket_type', 'priority_level', 'tag_list', 'ticket_status', 'create_dt', 'source_updated_at', 'solve_dt', 'wait_dt', 'open_dt', 'close_dt', 'servicer_group_id', 'servicer_group_name', 'creater_id', 'creater_name', 'agent_id', 'ticket_source', 'ticket_template_id', 'ticket_template_name', 'cc_group_id_list', 'custom_template_id', 'creater_type', 'current_node_field', 'current_node_field_value', 'node_field_into_time', 'query_ids', 'workflow_node_id', 'workflow_id', 'is_deleted', 'deleter_id', 'delete_dt', 'descript_attachments', 'create_year', 'create_month', 'create_month_label', 'last_sync_at', 'sync_status', 'sync_error', 'province', 'city', 'district', 'region_text', 'ticket_category', 'product_line', 'module_name', 'problem_type', 'customer_type', 'customer_industry', 'department_id', 'department_name', 'current_node_name', 'current_node_status', 'current_node_started_at', 'current_node_duration_seconds', 'created_at', 'updated_at')),
    'ticket_detail_custom_fields': frozenset(('id', 'ticket_id', 'ticket_template_id', 'create_dt', 'create_year', 'create_month', 'create_month_label', 'field_order', 'field_key', 'field_name', 'field_value', 'field_value_json', 'field_value_type', 'last_sync_at', 'created_at', 'updated_at')),
    'customers': frozenset(('customer_id', 'customer_name', 'customer_type', 'province', 'city', 'district', 'address', 'source_flags', 'source_updated_at', 'last_sync_at', 'created_at', 'updated_at', 'contact_name', 'phone', 'email', 'row_hash', 'sync_batch_id')),
    'contacts': frozenset(('contact_id', 'contact_name', 'phone', 'email', 'qq', 'wechat', 'customer_id', 'customer_name', 'department_name', 'position_name', 'source_flags', 'source_updated_at', 'last_sync_at', 'created_at', 'updated_at', 'fixed_phone', 'row_hash', 'sync_batch_id')),
    'sync_task_log': frozenset(('id', 'task_type', 'target_year', 'target_month', 'target_month_label', 'status', 'total_count', 'success_count', 'failed_count', 'skipped_count', 'started_at', 'finished_at', 'duration_seconds', 'error_message', 'extra_json', 'created_at')),
    'customer_history': frozenset(('customer_id', 'version_no', 'customer_name', 'customer_type', 'province', 'city', 'district', 'address', 'contact_name', 'phone', 'email', 'source_flags', 'source_updated_at', 'row_hash', 'sync_batch_id', 'effective_from', 'effective_to', 'is_current', 'created_at')),
    'contact_history': frozenset(('contact_id', 'version_no', 'contact_name', 'phone', 'fixed_phone', 'email', 'qq', 'wechat', 'customer_id', 'customer_name', 'department_name', 'position_name', 'source_flags', 'source_updated_at', 'row_hash', 'sync_batch_id', 'effective_from', 'effective_to', 'is_current', 'created_at')),
    'customer_contact_relation_history': frozenset(('contact_id', 'version_no', 'customer_id', 'customer_name', 'sync_batch_id', 'effective_from', 'effective_to', 'is_current', 'created_at')),
    'api_sync_batch': frozenset(('sync_batch_id', 'entity_type', 'status', 'fetched_count', 'raw_saved_count', 'inserted_count', 'changed_count', 'unchanged_count', 'failed_count', 'error_message', 'started_at', 'finished_at')),
    'api_raw_record': frozenset(('id', 'sync_batch_id', 'entity_type', 'source_name', 'source_record_id', 'payload_json', 'created_at')),
}


def is_satisfied(cursor: Any, database: str) -> bool:
    """Return true only when every frozen baseline column exists."""

    for table, required in _REQUIRED_COLUMNS.items():
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (database, table),
        )
        columns = {str(row[0]) for row in cursor.fetchall()}
        if not required.issubset(columns):
            return False
    return True


def apply(cursor: Any, database: str) -> None:
    """Create missing baseline tables and add only frozen compatibility columns."""

    for _, statement in _TABLE_DDLS:
        cursor.execute(statement)
    for table, alterations in _COMPATIBILITY_ALTERS.items():
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (database, table),
        )
        existing = {str(row[0]) for row in cursor.fetchall()}
        for column, statement in alterations:
            if column not in existing:
                cursor.execute(statement)
                existing.add(column)
