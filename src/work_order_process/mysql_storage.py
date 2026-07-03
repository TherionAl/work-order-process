"""MySQL 建表与工单详情入库。

项目里的工单详情最终落成两张表：
1. ticket_detail_main：一条工单一行，保存顶层字段；
2. ticket_detail_custom_fields：一条 custom_fields 字段一行，保存动态字段。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .api import ApiError, WorkOrderClient
from .config import MySQLConfig
from .dictionary import DataDictionary
from .resolver import TicketFieldResolver, resolve_ticket_detail_values


MAIN_FIELD_COLUMN_MAP = {
    "ticketId": "ticket_id",
    "custUserId": "cust_user_id",
    "subject": "subject",
    "descript": "descript",
    "servicerUserId": "servicer_user_id",
    "ccUserIdList": "cc_user_id_list",
    "ticketType": "ticket_type",
    "priorityLevel": "priority_level",
    "tagList": "tag_list",
    "ticketStatus": "ticket_status",
    "createDT": "create_dt",
    "updateDT": "update_dt",
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
}

DATETIME_COLUMNS = {
    "create_dt",
    "update_dt",
    "solve_dt",
    "wait_dt",
    "open_dt",
    "close_dt",
    "node_field_into_time",
    "delete_dt",
}

JSON_COLUMNS = {"descript_attachments"}


def ensure_mysql_schema(config: MySQLConfig) -> None:
    """创建数据库和两张工单详情表。"""

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


def import_ticket_detail_to_mysql(
    config: MySQLConfig,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    ticket_id: str,
) -> dict[str, Any]:
    """拉取单条工单详情，完成 value 替换后写入 MySQL。"""

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


def upsert_ticket_detail(
    config: MySQLConfig,
    main_row: dict[str, Any],
    custom_rows: list[dict[str, Any]],
) -> None:
    """用事务写入一条工单主表和对应自定义字段明细。"""

    ensure_mysql_schema(config)
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
                _upsert_main_row(cursor, main_row)
                cursor.execute("DELETE FROM ticket_detail_custom_fields WHERE ticket_id = %s", (main_row["ticket_id"],))
                if custom_rows:
                    _insert_custom_rows(cursor, custom_rows)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


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
                "field_order": index + 1,
                "field_key": _text_or_none(raw_item.get("key")) or "",
                "field_name": _text_or_none(value_item.get("key") or raw_item.get("key")),
                "field_value": _text_or_none(field_value),
                "field_value_json": _json_or_none(field_value) if isinstance(field_value, (dict, list)) else None,
                "field_value_type": _value_type(field_value),
            }
        )
    return rows


def _upsert_main_row(cursor: Any, row: dict[str, Any]) -> None:
    """执行主表 upsert。"""

    columns = list(MAIN_FIELD_COLUMN_MAP.values())
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(f"`{column}`" for column in columns)
    update_sql = ", ".join(f"`{column}` = VALUES(`{column}`)" for column in columns if column != "ticket_id")
    values = [row.get(column) for column in columns]
    cursor.execute(
        f"INSERT INTO ticket_detail_main ({column_sql}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_sql}, updated_at = CURRENT_TIMESTAMP",
        values,
    )


def _insert_custom_rows(cursor: Any, rows: list[dict[str, Any]]) -> None:
    """批量插入自定义字段明细。"""

    columns = [
        "ticket_id",
        "ticket_template_id",
        "field_order",
        "field_key",
        "field_name",
        "field_value",
        "field_value_json",
        "field_value_type",
    ]
    column_sql = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    values = [[row.get(column) for column in columns] for row in rows]
    cursor.executemany(
        f"INSERT INTO ticket_detail_custom_fields ({column_sql}) VALUES ({placeholders})",
        values,
    )


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


TICKET_DETAIL_MAIN_DDL = """
CREATE TABLE IF NOT EXISTS ticket_detail_main (
  ticket_id BIGINT NOT NULL COMMENT '工单ID',
  cust_user_id VARCHAR(255) NULL COMMENT '联系人',
  subject VARCHAR(1000) NULL COMMENT '标题',
  descript MEDIUMTEXT NULL COMMENT '描述',
  servicer_user_id VARCHAR(255) NULL COMMENT '客服',
  cc_user_id_list TEXT NULL COMMENT '抄送客服',
  ticket_type VARCHAR(100) NULL COMMENT '工单类型',
  priority_level VARCHAR(100) NULL COMMENT '优先级',
  tag_list TEXT NULL COMMENT '标签',
  ticket_status VARCHAR(100) NULL COMMENT '工单状态',
  create_dt DATETIME NULL COMMENT '创建时间',
  update_dt DATETIME NULL COMMENT '修改时间',
  solve_dt DATETIME NULL COMMENT '解决时间',
  wait_dt DATETIME NULL COMMENT '等待时间',
  open_dt DATETIME NULL COMMENT '开启时间',
  close_dt DATETIME NULL COMMENT '关闭时间',
  servicer_group_id VARCHAR(255) NULL COMMENT '客服组',
  creater_id VARCHAR(255) NULL COMMENT '创建人',
  agent_id VARCHAR(255) NULL COMMENT '服务商/代理商ID',
  ticket_source VARCHAR(100) NULL COMMENT '工单来源',
  ticket_template_id VARCHAR(255) NULL COMMENT '工单模板',
  cc_group_id_list TEXT NULL COMMENT '抄送客服组',
  custom_template_id VARCHAR(255) NULL COMMENT '自定义模板ID',
  creater_type VARCHAR(100) NULL COMMENT '创建人类型',
  current_node_field VARCHAR(255) NULL COMMENT '当前流程节点字段',
  current_node_field_value VARCHAR(255) NULL COMMENT '当前流程节点值',
  node_field_into_time DATETIME NULL COMMENT '进入节点时间',
  query_ids TEXT NULL COMMENT '查询ID集合',
  workflow_node_id VARCHAR(255) NULL COMMENT '工作流节点ID',
  workflow_id VARCHAR(255) NULL COMMENT '工作流ID',
  is_deleted VARCHAR(50) NULL COMMENT '是否删除',
  deleter_id VARCHAR(255) NULL COMMENT '删除人',
  delete_dt DATETIME NULL COMMENT '删除时间',
  descript_attachments JSON NULL COMMENT '描述附件JSON',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (ticket_id),
  KEY idx_ticket_template_id (ticket_template_id),
  KEY idx_ticket_status (ticket_status),
  KEY idx_create_dt (create_dt),
  KEY idx_update_dt (update_dt),
  KEY idx_servicer_user_id (servicer_user_id),
  KEY idx_servicer_group_id (servicer_group_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='工单详情主表'
"""


TICKET_DETAIL_CUSTOM_FIELDS_DDL = """
CREATE TABLE IF NOT EXISTS ticket_detail_custom_fields (
  id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  ticket_id BIGINT NOT NULL COMMENT '工单ID',
  ticket_template_id VARCHAR(255) NULL COMMENT '工单模板',
  field_order INT NOT NULL COMMENT '字段顺序',
  field_key VARCHAR(255) NOT NULL COMMENT '英文字段或原始字段key',
  field_name VARCHAR(255) NULL COMMENT '中文字段名',
  field_value MEDIUMTEXT NULL COMMENT '字段值文本',
  field_value_json JSON NULL COMMENT '字段值JSON，数组或对象时使用',
  field_value_type VARCHAR(50) NULL COMMENT '字段值类型',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_ticket_field_order (ticket_id, field_order),
  KEY idx_ticket_id (ticket_id),
  KEY idx_template_field (ticket_template_id, field_name),
  KEY idx_field_name (field_name),
  KEY idx_field_key (field_key),
  CONSTRAINT fk_custom_fields_ticket
    FOREIGN KEY (ticket_id)
    REFERENCES ticket_detail_main (ticket_id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='工单自定义字段明细表'
"""
