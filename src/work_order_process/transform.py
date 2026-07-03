"""通用数据转换工具。

这里保留的是较早阶段使用的通用转换能力：按日期过滤工单、随机抽样、
将工单和本地已导出的客户/联系人数据做简单关联，以及批量中文化字段。
新的工单详情三段式导出逻辑在 resolver.py 中。
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any

from .dictionary import DataDictionary


DATE_KEYS = ("createDT", "createTime", "createdAt", "created_at", "createDate", "updateDT")
CONTACT_ID_KEYS = ("custUserId", "contactId", "contacterId", "cId")
COMPANY_ID_KEYS = ("companyId", "userId", "uId", "customerId")


def filter_tickets_since(tickets: list[dict], since: str) -> list[dict]:
    """筛选创建/更新时间不早于 since 的工单；缺少时间时保留。"""

    since_dt = datetime.fromisoformat(since)
    filtered: list[dict] = []
    for ticket in tickets:
        ticket_dt = _first_datetime(ticket, DATE_KEYS)
        if ticket_dt is None or ticket_dt >= since_dt:
            filtered.append(ticket)
    return filtered


def random_sample(items: list[dict], size: int, seed: int | None = None) -> list[dict]:
    """从列表中随机抽样；传入 seed 时结果可复现。"""

    if len(items) <= size:
        return list(items)
    rng = random.Random(seed)
    return rng.sample(items, size)


def enrich_tickets(
    tickets: list[dict],
    contacts: list[dict],
    customers: list[dict],
    dictionary: DataDictionary,
) -> list[dict]:
    """用已导出的联系人和客户列表为工单补充关联信息。

    这是离线关联方式：只依赖本地列表数据，不额外调用详情接口。
    """

    contacts_by_id = _index_by_any(contacts, ("cId", "cid", "id", "contactId", "contacterId"))
    customers_by_id = _index_by_any(customers, ("uId", "uid", "id", "userId", "customerId", "companyId"))

    enriched: list[dict] = []
    for ticket in tickets:
        contact = contacts_by_id.get(_first_value(ticket, CONTACT_ID_KEYS))
        customer = None
        if contact:
            customer = customers_by_id.get(_first_value(contact, COMPANY_ID_KEYS))
        if customer is None:
            customer = customers_by_id.get(_first_value(ticket, COMPANY_ID_KEYS))

        enriched.append(
            {
                "工单": dictionary.translate_record("tickets", ticket),
                "联系人": dictionary.translate_record("contacter", contact) if contact else None,
                "客户": dictionary.translate_record("user", customer) if customer else None,
            }
        )
    return enriched


def translate_many(dictionary: DataDictionary, table: str, rows: list[dict]) -> list[dict]:
    """批量把某张表的记录 key 翻译成中文。"""

    return [dictionary.translate_record(table, row) for row in rows]


def _index_by_any(rows: list[dict], keys: tuple[str, ...]) -> dict[Any, dict]:
    """按多个可能的主键字段建立索引，兼容接口字段命名差异。"""

    index: dict[Any, dict] = {}
    for row in rows:
        key = _first_value(row, keys)
        if key is not None:
            index[key] = row
            index[str(key)] = row
    return index


def _first_value(row: dict | None, keys: tuple[str, ...]) -> Any:
    """从记录中按候选字段名返回第一个非空值，大小写不敏感。"""

    if not row:
        return None
    lower = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        value = lower.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _first_datetime(row: dict, keys: tuple[str, ...]) -> datetime | None:
    """从记录的候选时间字段中解析 datetime。"""

    value = _first_value(row, keys)
    if not value:
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
