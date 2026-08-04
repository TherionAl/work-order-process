"""客户/联系人实体结构化工具。

本模块只做字段标准化，不访问接口、不写 MySQL：
1. 客户接口和公司接口返回的数据统一整理为 `customers` 行；
2. 联系人接口和公司联系人接口返回的数据统一整理为 `contacts` 行。

历史接口命名存在差异，所以这里按多个候选字段名提取同一语义字段。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
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

CUSTOMER_HASH_FIELDS = tuple(CUSTOMER_COLUMNS)
CONTACT_HASH_FIELDS = tuple(CONTACT_COLUMNS)


def build_customer_row(record: dict[str, Any], source_flag: str) -> dict[str, Any]:
    """把客户/公司接口记录转换成 `customers` 标准行。"""

    address = text_or_none(first_value(record, "address", "addr"))
    city = text_or_none(first_value(record, "city", "cityName", "area2", "area2Name"))
    district = text_or_none(
        first_value(record, "district", "districtName", "area3", "area3Name")
    )

    # 从地址中解析城市/区县（当 API 未直接返回时）
    if city is None or district is None:
        parsed_city, parsed_district = parse_region_from_address(address)
        city = city or parsed_city
        district = district or parsed_district

    return {
        "customer_id": require_text(
            first_value(record, "uId", "uid", "id", "userId", "customerId", "companyId"),
            "customer_id",
        ),
        "customer_name": text_or_none(
            first_value(record, "companyName", "customerName", "company_name", "name", "userName")
        ),
        "customer_type": text_or_none(
            first_value(record, "customerType", "customer_type", "rank", "type", "nature")
        ),
        "province": text_or_none(
            first_value(record, "province", "provinceName", "area", "areaName", "userGroup")
        ),
        "city": city,
        "district": district,
        "address": address,
        "contact_name": text_or_none(
            first_value(record, "contactor", "contactName", "contact_name", "linkman")
        ),
        "phone": text_or_none(first_value(record, "mobile", "phone", "tel", "telephone")),
        "email": text_or_none(first_value(record, "email", "mail")),
        "source_flags": source_flag,
        "source_updated_at": parse_datetime(
            first_value(record, "updateTime", "updateDT", "updated_at", "modifyTime")
        ),
    }


def build_contact_row(record: dict[str, Any], source_flag: str) -> dict[str, Any]:
    """把联系人/公司联系人接口记录转换成 `contacts` 标准行。"""

    return {
        "contact_id": require_text(
            first_value(record, "cId", "cid", "id", "contactId", "contacterId"),
            "contact_id",
        ),
        "contact_name": text_or_none(
            first_value(record, "realName", "name", "contactName", "contact_name")
        ),
        "phone": text_or_none(first_value(record, "mobile", "phoneNumber", "mobilePhone")),
        "fixed_phone": text_or_none(first_value(record, "fixnumber", "fixedPhone", "tel", "phone")),
        "email": text_or_none(first_value(record, "email", "mail")),
        "qq": text_or_none(first_value(record, "QQ", "qq")),
        "wechat": text_or_none(first_value(record, "wechat", "weChat", "wx")),
        "customer_id": text_or_none(
            first_value(record, "companyId", "userId", "uId", "customerId")
        ),
        "customer_name": text_or_none(
            first_value(record, "companyName", "customerName", "customer_name")
        ),
        "department_name": text_or_none(
            first_value(record, "department", "departmentName", "deptName")
        ),
        "position_name": text_or_none(first_value(record, "position", "positionName", "jobTitle")),
        "source_flags": source_flag,
        "source_updated_at": parse_datetime(
            first_value(record, "updateTime", "updateDT", "updated_at", "modifyTime")
        ),
    }


def parse_region_from_address(address: str | None) -> tuple[str | None, str | None]:
    """从地址文本中解析城市和区县。

    示例：
        "重庆市巴南区龙洲湾街道龙海大道6号" → ("重庆市", "巴南区")
        "四川省成都市武侯区天府大道" → ("成都市", "武侯区")
        "广东省深圳市南山区科技园" → ("深圳市", "南山区")
        "山东省青岛市市南区香港中路" → ("青岛市", "市南区")
        "广西壮族自治区南宁市青秀区民族大道" → ("南宁市", "青秀区")
    """
    if not address:
        return None, None

    city = None
    district = None

    # 省份/自治区前缀（可选）
    province_prefix = r'(?:.*?省|.*?自治区|.*?特别行政区)?'

    # 匹配直辖市：北京市/上海市/天津市/重庆市 + 区县
    municipality_match = re.match(r'^(北京|上海|天津|重庆)市([^\s街道省市县]{1,6}?)区', address)
    if municipality_match:
        city = municipality_match.group(1) + "市"
        district = municipality_match.group(2) + "区"
        return city, district
    municipality_county = re.match(r'^(北京|上海|天津|重庆)市([^\s街道省市]{1,6}?)县', address)
    if municipality_county:
        city = municipality_county.group(1) + "市"
        district = municipality_county.group(2) + "县"
        return city, district

    # 匹配普通城市：XX市 + XX区（含"市南区"这类特殊区县名）
    city_district = re.match(
        r'^[\s]*?' + province_prefix + r'([^\s省市]{1,4}?)市([^\s街道]{0,6}?)区', address
    )
    if city_district:
        city = city_district.group(1) + "市"
        district_raw = city_district.group(2)
        # 处理"市南区"这种情况：group(2)为空，实际是"市"+后面的"南区"
        if district_raw:
            district = district_raw + "区"
        else:
            # 格式是"XX市YY区"，如"青岛市市南区"
            # 需要重新提取"市"后面的区名
            after_city = address[address.find(city):]
            district_match = re.match(r'^市([^\s街道]{1,6}?)区', after_city)
            if district_match:
                district = district_match.group(1) + "区"
        if district:
            return city, district

    # 匹配 XX市 + XX县
    city_county = re.match(
        r'^[\s]*?' + province_prefix + r'([^\s省市]{1,4}?)市([^\s街道省市]{1,6}?)县', address
    )
    if city_county:
        city = city_county.group(1) + "市"
        district = city_county.group(2) + "县"
        return city, district

    # 只匹配到市（无区县）
    only_city = re.match(r'^[\s]*?' + province_prefix + r'([^\s省市]{1,4}?)市', address)
    if only_city:
        city = only_city.group(1) + "市"

    return city, district


def entity_row_hash(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    """Return a stable business-field hash for change detection."""

    payload = {field: row.get(field) for field in fields}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
            return datetime.strptime(text[: 19 if "%S" in fmt else 10], fmt)
        except ValueError:
            continue
    return None
