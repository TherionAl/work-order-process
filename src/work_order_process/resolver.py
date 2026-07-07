"""工单详情字段值解析与中文化。

用户最终需要三份文件：
1. ticket_details_raw.json：接口返回的原始工单详情；
2. ticket_details_value_resolved.json：英文 key 不变，只把可识别的 ID/枚举 value 替换成人名、组名、中文枚举；
3. ticket_details_chinese.json：在第二份基础上，再用数据字典把 key 翻译成中文。

本模块负责第 2、3 步，并尽量只替换接口文档中能通过其它接口确认的字段。
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .api import WorkOrderClient
from .dictionary import DataDictionary


TICKET_TYPE = {"1": "问题", "2": "事务", "3": "故障", "4": "任务"}
PRIORITY_LEVEL = {"1": "低", "2": "正常", "3": "高", "4": "紧急"}
TICKET_STATUS = {"1": "新建", "2": "已开启", "3": "待回应", "4": "已解决", "5": "已关闭", "6": "已关闭"}
CREATER_TYPE = {"0": "客服", "1": "客户"}
YES_NO = {"0": "否", "1": "是"}


class TicketFieldResolver:
    """工单自定义字段字典。

    字段接口会返回 field_xxx 对应的中文名称，以及下拉/级联等字段的选项 key/value。
    这里把它整理成两个索引，供 custom_fields 和 currentNodeField 相关字段快速替换。
    """

    def __init__(self, fields: list[dict[str, Any]], extra_option_fields: list[dict[str, Any]] | None = None) -> None:
        self.fields_by_key = {str(field.get("key")): field for field in fields if field.get("key")}
        self.options_by_field: dict[str, dict[str, str]] = {}
        self.options_by_key: dict[str, str] = {}
        for field in fields:
            field_key = str(field.get("key") or "")
            options = self._collect_options(field.get("custom_field_options"))
            if field_key:
                self.options_by_field[field_key] = options
            self.options_by_key.update(options)
        for field in extra_option_fields or []:
            self.options_by_key.update(self._collect_options(field.get("custom_field_options")))

    def field_name(self, field_key: Any) -> Any:
        """把 field_xxx 替换为字段中文名称；找不到时保留原值。"""

        key = str(field_key or "").strip()
        field = self.fields_by_key.get(key)
        return _first_nonempty(field.get("name") if field else None, field_key)

    def option_value(self, value: Any, field_key: Any = None) -> Any:
        """把字段选项 ID 替换为选项中文值，支持标量、列表和嵌套字典。"""

        if isinstance(value, list):
            return [self.option_value(item, field_key) for item in value]
        if isinstance(value, dict):
            return {key: self.option_value(item, field_key) for key, item in value.items()}
        text = str(value).strip() if value is not None else ""
        if not text:
            return value
        field_options = self.options_by_field.get(str(field_key or ""), {})
        return field_options.get(text) or self.options_by_key.get(text) or value

    def resolve_custom_fields(self, custom_fields: Any, client: WorkOrderClient) -> Any:
        """替换 custom_fields 列表中每个字段的 key 和 value。"""

        if not isinstance(custom_fields, list):
            return custom_fields

        resolved: list[Any] = []
        for item in custom_fields:
            if not isinstance(item, dict):
                resolved.append(item)
                continue
            field_key = item.get("key")
            new_item = copy.deepcopy(item)
            new_item["key"] = self.field_name(field_key)
            if "value" in new_item:
                new_item["value"] = self.resolve_field_value(field_key, new_item["value"], client)
            resolved.append(new_item)
        return resolved

    def resolve_field_value(self, field_key: Any, value: Any, client: WorkOrderClient) -> Any:
        """按字段类型或字段 key 特征替换自定义字段 value。"""

        if str(field_key or "").startswith("record_serviceruserid"):
            return _resolve_support_custom_value(value, client)
        return self.option_value(value, field_key)

    def _collect_options(self, options: Any) -> dict[str, str]:
        """递归收集下拉、复选和级联字段中的选项 key/value。"""

        collected: dict[str, str] = {}
        if not isinstance(options, list):
            return collected
        for option in options:
            if not isinstance(option, dict):
                continue
            key = option.get("key")
            value = option.get("value") or option.get("name") or option.get("label")
            if key is not None and value is not None:
                collected[str(key)] = str(value)
            for child_key in ("children", "childs", "options", "items"):
                collected.update(self._collect_options(option.get(child_key)))
        return collected


def resolve_ticket_detail_values(
    detail: dict[str, Any],
    client: WorkOrderClient,
    field_resolver: TicketFieldResolver | None = None,
) -> dict[str, Any]:
    """把单条工单详情中的外键和枚举值替换成更可读的中文值。

    这里不会修改字段名，也不会覆盖原始文件；原始 ID 可通过
    ticket_details_raw.json 对照查看。
    """

    row = copy.deepcopy(detail)
    field_resolver = field_resolver or TicketFieldResolver(client.fetch_ticket_fields(), client.fetch_company_fields())

    _replace_contact(row, client)
    _replace_support(row, "servicerUserId", client)
    _replace_support(row, "createrId", client)
    _replace_support(row, "deleterId", client)
    _replace_support_list(row, "ccUserIdList", client)
    _replace_support_group(row, "servicerGroupId", client)
    _replace_support_group_list(row, "ccGroupIdList", client)
    _replace_ticket_template(row, client)
    _replace_ticket_custom_fields(row, client, field_resolver)
    _replace_unix_timestamp(row, "nodeFieldIntoTime")

    _replace_enum(row, "ticketType", TICKET_TYPE)
    _replace_enum(row, "priorityLevel", PRIORITY_LEVEL)
    _replace_enum(row, "ticketStatus", TICKET_STATUS)
    _replace_enum(row, "createrType", CREATER_TYPE)
    _replace_enum(row, "isDeleted", YES_NO)

    _extract_analytic_dimensions(row)

    return row



def _replace_contact(row: dict[str, Any], client: WorkOrderClient) -> None:
    """用联系人详情接口返回的 name 补充 cust_user_name；保留 custUserId 原值不变。"""

    contact_id = str(row.get("custUserId") or "").strip()
    if not contact_id:
        return
    contact = client.fetch_contact_detail(contact_id)
    if not contact:
        row["cust_user_name"] = contact_id
        return
    row["cust_user_name"] = _first_nonempty(contact.get("name"), contact_id)


def _replace_support(row: dict[str, Any], key: str, client: WorkOrderClient) -> None:
    """用客服详情接口返回的 name 补充 name 字段；保留原始 ID 字段不变。"""

    support_id = str(row.get(key) or "").strip()
    if not support_id or support_id == "0":
        return
    name_field = _support_name_field(key)
    support = client.fetch_support_detail(support_id)
    if not support:
        row[name_field] = support_id
        return
    row[name_field] = _first_nonempty(support.get("name"), support_id)


def _replace_support_group(row: dict[str, Any], key: str, client: WorkOrderClient) -> None:
    """用客服组详情接口返回的 sgName 补充 name 字段；保留原始 ID 字段不变。"""

    group_id = str(row.get(key) or "").strip()
    if not group_id or group_id == "0":
        return
    name_field = _support_group_name_field(key)
    support_group = client.fetch_support_group_detail(group_id)
    if not support_group:
        row[name_field] = group_id
        return
    row[name_field] = _first_nonempty(support_group.get("sgName"), group_id)


def _replace_support_list(row: dict[str, Any], key: str, client: WorkOrderClient) -> None:
    """把逗号分隔的客服组 ID 列表逐个替换为客服姓名；保留原值不变。"""

    ids = _split_id_list(row.get(key))
    if not ids:
        return
    names: list[str] = []
    for item in ids:
        support = client.fetch_support_detail(item)
        names.append(_first_nonempty(support.get("name") if support else None, item))
    row[key] = ",".join(names)


def _replace_support_group_list(row: dict[str, Any], key: str, client: WorkOrderClient) -> None:
    """把逗号分隔的客服组 ID 列表逐个替换为客服组名称；保留原值不变。"""

    ids = _split_id_list(row.get(key))
    if not ids:
        return
    names: list[str] = []
    for item in ids:
        support_group = client.fetch_support_group_detail(item)
        names.append(_first_nonempty(support_group.get("sgName") if support_group else None, item))
    row[key] = ",".join(names)


def _replace_ticket_template(row: dict[str, Any], client: WorkOrderClient) -> None:
    """用工单模板详情接口返回的 ticketTemplateName 补充 ticket_template_name；保留 template_id 原值不变。"""

    template_id = str(row.get("ticketTemplateId") or "").strip()
    if not template_id or template_id == "0":
        return
    template = client.fetch_ticket_template_detail(template_id)
    if not template:
        row["ticket_template_name"] = template_id
        return
    row["ticket_template_name"] = _first_nonempty(template.get("ticketTemplateName"), template_id)


def _support_name_field(key: str) -> str:
    """给定原始客服 ID 字段名，返回对应的人员姓名字段名。"""

    mapping = {
        "servicerUserId": "servicer_user_name",
        "createrId": "creater_name",
        "deleterId": "deleter_name",
    }
    return mapping.get(key, key + "_name")


def _support_group_name_field(key: str) -> str:
    if key == "servicerGroupId":
        return "servicer_group_name"
    return key + "_name"


def _replace_ticket_custom_fields(
    row: dict[str, Any],
    client: WorkOrderClient,
    field_resolver: TicketFieldResolver,
) -> None:
    """替换工单详情中的自定义字段名称和选项值。"""

    current_field_key = row.get("currentNodeField")
    if current_field_key:
        row["currentNodeField"] = field_resolver.field_name(current_field_key)
    if row.get("currentNodeFieldValue") not in (None, ""):
        row["currentNodeFieldValue"] = field_resolver.option_value(row.get("currentNodeFieldValue"), current_field_key)
    if "custom_fields" in row:
        row["custom_fields"] = field_resolver.resolve_custom_fields(row.get("custom_fields"), client)


def _resolve_support_custom_value(value: Any, client: WorkOrderClient) -> Any:
    """把自定义字段里的客服 ID 替换为客服姓名。"""

    if isinstance(value, list):
        return [_resolve_support_custom_value(item, client) for item in value]
    support_id = str(value or "").strip()
    if not support_id:
        return value
    support = client.fetch_support_detail(support_id)
    if not support:
        return value
    return _first_nonempty(support.get("name"), value)


def _replace_enum(row: dict[str, Any], key: str, mapping: dict[str, str]) -> None:
    """根据本地枚举字典替换工单类型、优先级、状态等字段。"""

    value = row.get(key)
    if value is None or str(value).strip() == "":
        return
    row[key] = mapping.get(str(value).strip(), value)


def _replace_unix_timestamp(row: dict[str, Any], key: str) -> None:
    """把秒级 Unix 时间戳替换成可读时间；0 或空值保留原值。"""

    value = row.get(key)
    text = str(value or "").strip()
    if not text or text == "0" or not text.isdigit():
        return
    row[key] = datetime.fromtimestamp(int(text)).strftime("%Y-%m-%d %H:%M:%S")


def _split_id_list(value: Any) -> list[str]:
    """兼容列表、英文逗号和中文逗号形式的 ID 集合。"""

    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]


def _first_nonempty(*values: Any) -> Any:
    """返回第一个非空值，用作接口字段缺失时的兜底。"""

    for value in values:
        if value is not None and str(value).strip():
            return value
    return ""


# ---------------------------------------------------------------------------
# 分析维度提取：从 custom_fields 中抽主表分析列
# ---------------------------------------------------------------------------

# field_name 关键字 → 分析维度列名
ANALYTIC_FIELD_KEYWORDS: dict[str, list[str]] = {
    "province": ["省", "省份"],
    "city": ["市", "城市"],
    "district": ["区", "县", "地区"],
    "region_text": ["地区", "区域"],
    "product_line": ["产品线", "产品"],
    "module_name": ["模块"],
    "problem_type": ["问题类型", "问题分类"],
    "customer_type": ["客户类型", "客户性质"],
    "customer_industry": ["行业"],
    "department_name": ["部门"],
    "current_node_name": ["节点"],
    "current_node_status": ["状态"],
}


def _clean_analytic_value(value: str) -> str:
    """清洗分析维度原始值：去掉行政区划/地区编码前缀。

    例如 "140000 山西省" → "山西省"，"010" → ""。
    """

    text = value.strip()
    # 去掉开头的 6 位行政区划编码 + 可选空格
    text = re.sub(r"^\d{6}\s*", "", text)
    # 去掉开头的 2-4 位短编码 + 可选空格（如 "010 北京市"）
    text = re.sub(r"^\d{2,4}\s+", "", text)
    # 去掉括号内的编码
    text = re.sub(r"[（(]\d+[）)]\s*", "", text)
    return text.strip()


def _extract_analytic_dimensions(row: dict[str, Any]) -> None:
    """从 resolved custom_fields 中提取高频分析维度，注入到 row 顶层。"""

    custom_fields = row.get("custom_fields")
    if not isinstance(custom_fields, list):
        return

    for item in custom_fields:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("key") or "").strip()
        field_value = item.get("value")
        if not field_name:
            continue
        for analytic_key, keywords in ANALYTIC_FIELD_KEYWORDS.items():
            if analytic_key in row and row[analytic_key]:
                continue
            if any(keyword in field_name for keyword in keywords):
                if isinstance(field_value, (dict, list)):
                    row[analytic_key] = _clean_analytic_value(
                        json.dumps(field_value, ensure_ascii=False),
                    )
                elif field_value is not None and str(field_value).strip():
                    row[analytic_key] = _clean_analytic_value(str(field_value))
                break

    # current_node_started_at 来自 nodeFieldIntoTime（已在 _replace_unix_timestamp 中转为可读字符串）
    node_time = row.get("nodeFieldIntoTime") or row.get("node_field_into_time")
    if node_time and not row.get("current_node_started_at"):
        row["current_node_started_at"] = node_time
