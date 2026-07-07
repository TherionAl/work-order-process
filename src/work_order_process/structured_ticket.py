"""工单详情结构化转换工具。

本模块只负责把接口返回的工单详情整理成更稳定的结构：
1. `ticket_detail_main`：顶层字段，一条工单一行；
2. `ticket_detail_custom_fields`：动态自定义字段，一条字段一行；
3. Excel 导出行：用于人工检查“英文字段、中文字段、值”的对应关系。

MySQL 入库和 Excel 脚本都复用这里的函数，后续新增字段或调整规则时只需要改一处。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .dictionary import DataDictionary


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


def build_ticket_detail_main_row(value_detail: dict[str, Any]) -> dict[str, Any]:
    """把 value_resolved 工单详情顶层字段转换为 MySQL 主表行。"""

    row: dict[str, Any] = {}
    for api_key, column in MAIN_FIELD_COLUMN_MAP.items():
        value = value_detail.get(api_key)
        if column == "ticket_id":
            row[column] = int(str(value))
        elif column in DATETIME_COLUMNS:
            row[column] = to_datetime(value)
        elif column in JSON_COLUMNS:
            row[column] = json_or_none(value)
        else:
            row[column] = text_or_none(value)
    return row


def build_ticket_detail_custom_field_rows(
    raw_detail: dict[str, Any],
    value_detail: dict[str, Any],
) -> list[dict[str, Any]]:
    """把 `custom_fields` 转换为 MySQL 自定义字段明细行。"""

    ticket_id = int(str(raw_detail.get("ticketId") or value_detail.get("ticketId")))
    template_id = text_or_none(value_detail.get("ticketTemplateId") or raw_detail.get("ticketTemplateId"))
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
                "field_key": text_or_none(raw_item.get("key")) or "",
                "field_name": text_or_none(value_item.get("key") or raw_item.get("key")),
                "field_value": text_or_none(field_value),
                "field_value_json": json_or_none(field_value) if isinstance(field_value, (dict, list)) else None,
                "field_value_type": value_type(field_value),
            }
        )
    return rows


def build_main_excel_rows(
    raw_detail: dict[str, Any],
    value_detail: dict[str, Any],
    dictionary: DataDictionary,
) -> list[list[Any]]:
    """把工单顶层字段整理成 Excel 主表 sheet 行。"""

    ticket_id = raw_detail.get("ticketId") or value_detail.get("ticketId") or ""
    rows: list[list[Any]] = [["工单ID", "英文字段", "中文字段", "值", "值类型"]]
    for key in raw_detail:
        if key == "custom_fields":
            continue
        value = value_detail.get(key, raw_detail.get(key))
        rows.append([ticket_id, key, dictionary.label("tickets", str(key)), stringify_value(value), value_type(value)])
    return rows


def build_custom_field_excel_rows(raw_detail: dict[str, Any], value_detail: dict[str, Any]) -> list[list[Any]]:
    """把 `custom_fields` 动态字段整理成 Excel 明细 sheet 行。"""

    ticket_id = raw_detail.get("ticketId") or value_detail.get("ticketId") or ""
    template_id = value_detail.get("ticketTemplateId") or raw_detail.get("ticketTemplateId") or ""
    raw_fields = raw_detail.get("custom_fields") if isinstance(raw_detail.get("custom_fields"), list) else []
    value_fields = value_detail.get("custom_fields") if isinstance(value_detail.get("custom_fields"), list) else []

    rows: list[list[Any]] = [["工单ID", "工单模板", "字段顺序", "英文字段", "中文字段", "值", "值类型"]]
    max_len = max(len(raw_fields), len(value_fields))
    for index in range(max_len):
        raw_item = raw_fields[index] if index < len(raw_fields) and isinstance(raw_fields[index], dict) else {}
        value_item = value_fields[index] if index < len(value_fields) and isinstance(value_fields[index], dict) else {}
        field_value = value_item.get("value", raw_item.get("value"))
        rows.append(
            [
                ticket_id,
                template_id,
                index + 1,
                raw_item.get("key", ""),
                value_item.get("key", raw_item.get("key", "")),
                stringify_value(field_value),
                value_type(field_value),
            ]
        )
    return rows


def to_datetime(value: Any) -> datetime | None:
    """把接口里的时间字符串转成 `datetime`。"""

    text = str(value or "").strip()
    if not text or text == "0":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def text_or_none(value: Any) -> str | None:
    """普通字段转文本；数组和对象转 JSON 字符串。"""

    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text if text else None


def json_or_none(value: Any) -> str | None:
    """把 JSON 字段转成 MySQL 可接收的 JSON 字符串。"""

    if value in (None, ""):
        return None
    return json.dumps(value, ensure_ascii=False)


def stringify_value(value: Any) -> str:
    """把复杂值转成 JSON 字符串，普通值直接转文本。"""

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def value_type(value: Any) -> str:
    """返回字段值类型，方便后续判断是否需要再拆分。"""

    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    if value is None:
        return "null"
    return type(value).__name__
