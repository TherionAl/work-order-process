"""客户/联系人实体结构化工具。

本模块只做字段标准化，不访问接口、不写 MySQL：
1. 客户接口和公司接口返回的数据统一整理为 `customers` 行；
2. 联系人接口和公司联系人接口返回的数据统一整理为 `contacts` 行。

历史接口命名存在差异，所以这里按多个候选字段名提取同一语义字段。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


CUSTOMER_COLUMNS = [
    "customer_id",
    "customer_name",
    "customer_type",
    "province",
    "city",
    "district",
    "address",
    "contact_name",
    "phone",
    "email",
    "source_flags",
    "source_updated_at",
]

CONTACT_COLUMNS = [
    "contact_id",
    "contact_name",
    "phone",
    "fixed_phone",
    "email",
    "qq",
    "wechat",
    "customer_id",
    "customer_name",
    "department_name",
    "position_name",
    "source_flags",
    "source_updated_at",
]


def build_customer_row(record: dict[str, Any], source_flag: str) -> dict[str, Any]:
    """把客户/公司接口记录转换成 `customers` 标准行。"""

    return {
        "customer_id": require_text(
            first_value(record, "uId", "uid", "id", "userId", "customerId", "companyId"),
            "customer_id",
        ),
        "customer_name": text_or_none(
            first_value(record, "companyName", "customerName", "company_name", "name", "userName")
        ),
        "customer_type": text_or_none(first_value(record, "customerType", "customer_type", "rank", "type", "nature")),
        "province": text_or_none(first_value(record, "province", "provinceName", "area", "areaName")),
        "city": text_or_none(first_value(record, "city", "cityName", "area2", "area2Name")),
        "district": text_or_none(first_value(record, "district", "districtName", "area3", "area3Name")),
        "address": text_or_none(first_value(record, "address", "addr")),
        "contact_name": text_or_none(first_value(record, "contactor", "contactName", "contact_name", "linkman")),
        "phone": text_or_none(first_value(record, "mobile", "phone", "tel", "telephone")),
        "email": text_or_none(first_value(record, "email", "mail")),
        "source_flags": source_flag,
        "source_updated_at": parse_datetime(first_value(record, "updateTime", "updateDT", "updated_at", "modifyTime")),
    }


def build_contact_row(record: dict[str, Any], source_flag: str) -> dict[str, Any]:
    """把联系人/公司联系人接口记录转换成 `contacts` 标准行。"""

    return {
        "contact_id": require_text(
            first_value(record, "cId", "cid", "id", "contactId", "contacterId"),
            "contact_id",
        ),
        "contact_name": text_or_none(first_value(record, "realName", "name", "contactName", "contact_name")),
        "phone": text_or_none(first_value(record, "mobile", "phoneNumber", "mobilePhone")),
        "fixed_phone": text_or_none(first_value(record, "fixnumber", "fixedPhone", "tel", "phone")),
        "email": text_or_none(first_value(record, "email", "mail")),
        "qq": text_or_none(first_value(record, "QQ", "qq")),
        "wechat": text_or_none(first_value(record, "wechat", "weChat", "wx")),
        "customer_id": text_or_none(first_value(record, "companyId", "userId", "uId", "customerId")),
        "customer_name": text_or_none(first_value(record, "companyName", "customerName", "customer_name")),
        "department_name": text_or_none(first_value(record, "department", "departmentName", "deptName")),
        "position_name": text_or_none(first_value(record, "position", "positionName", "jobTitle")),
        "source_flags": source_flag,
        "source_updated_at": parse_datetime(first_value(record, "updateTime", "updateDT", "updated_at", "modifyTime")),
    }


def first_value(record: dict[str, Any], *keys: str) -> Any:
    """按候选字段名返回第一个非空值，大小写不敏感。"""

    lower = {str(key).lower(): value for key, value in record.items()}
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
        value = lower.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def require_text(value: Any, field_name: str) -> str:
    """主键字段必须非空。"""

    text = text_or_none(value)
    if text is None:
        raise ValueError(f"Missing required field: {field_name}")
    return text


def text_or_none(value: Any) -> str | None:
    """普通字段转文本；数组和对象转 JSON 字符串。"""

    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text if text else None


def parse_datetime(value: Any) -> datetime | None:
    """兼容常见接口时间格式。"""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("/", "-")
    if text.isdigit() and len(text) >= 10:
        return datetime.fromtimestamp(int(text[:10]))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19 if "%S" in fmt else 10], fmt)
        except ValueError:
            continue
    return None
