"""Shared, read-only helpers for the Hubei ticket analysis scripts."""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv

from .config import PROJECT_ROOT, MySQLConfig

DEFAULT_STATUSES = ("已解决", "已关闭")
DEFAULT_PROVINCE = "湖北省"
DEFAULT_SERVICE_CATALOG = "生产环境异常处理"
DEFAULT_ASSISTANCE_FIELD = "【一线】申请协助时间"


@dataclass(frozen=True)
class AnalysisScope:
    """The reproducible ticket filter shared by Hubei report scripts."""

    start: datetime
    end: datetime
    province: str = DEFAULT_PROVINCE
    service_catalog_contains: str = DEFAULT_SERVICE_CATALOG
    assistance_field: str = DEFAULT_ASSISTANCE_FIELD
    statuses: tuple[str, ...] = DEFAULT_STATUSES

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("start must be earlier than end")
        if not self.province.strip():
            raise ValueError("province must not be empty")
        if not self.service_catalog_contains.strip():
            raise ValueError("service_catalog_contains must not be empty")


def add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the standard, read-only report filter arguments to ``parser``."""

    parser.add_argument("--start", type=_parse_datetime, help="inclusive ISO-8601 start time")
    parser.add_argument("--end", type=_parse_datetime, help="exclusive ISO-8601 end time")
    parser.add_argument("--province", default=DEFAULT_PROVINCE, help="province to analyse")
    parser.add_argument(
        "--service-catalog-contains",
        default=DEFAULT_SERVICE_CATALOG,
        help="substring required in 【服务目录】",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="directory for the generated Excel report",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="optional maximum number of matched tickets",
    )


def scope_from_arguments(args: argparse.Namespace, *, now: datetime | None = None) -> AnalysisScope:
    """Build the default 30-day scope, allowing CLI values to override it."""

    reference = now or datetime.now()
    end = args.end or reference
    start = args.start or end - timedelta(days=30)
    return AnalysisScope(
        start=start,
        end=end,
        province=args.province,
        service_catalog_contains=args.service_catalog_contains,
    )


def mysql_config_from_environment() -> MySQLConfig:
    """Load the project-local read connection configuration."""

    load_dotenv(PROJECT_ROOT / ".env")
    return MySQLConfig(
        host=os.getenv("WORKORDER_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("WORKORDER_MYSQL_PORT", "3306")),
        user=os.getenv("WORKORDER_MYSQL_USER", "workorder"),
        password=os.getenv("WORKORDER_MYSQL_PASSWORD", ""),
        database=os.getenv("WORKORDER_MYSQL_DATABASE", "work_order_datalake"),
    )


def build_ticket_version_predicate(
    ticket_versions: Iterable[tuple[int, datetime]],
) -> tuple[str, tuple[object, ...]]:
    """Return a parameterized predicate that preserves ticket-version identity."""

    versions = list(ticket_versions)
    if not versions:
        return "1 = 0", ()
    placeholders = ", ".join("(%s, %s)" for _ in versions)
    params = tuple(value for ticket_id, create_dt in versions for value in (ticket_id, create_dt))
    return f"(ticket_id, create_dt) IN ({placeholders})", params


def index_custom_fields_by_ticket_version(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[int, datetime], dict[str, Any]]:
    """Index custom fields without mixing different versions of one ticket."""

    indexed: dict[tuple[int, datetime], dict[str, Any]] = {}
    for row in rows:
        ticket_id = row["ticket_id"]
        create_dt = row["create_dt"]
        if create_dt is None:
            raise ValueError(f"custom field for ticket {ticket_id} has no create_dt")
        field_name = row.get("field_name") or row.get("field_key")
        if not field_name:
            continue
        fields = indexed.setdefault((ticket_id, create_dt), {})
        value = row.get("field_value")
        existing = fields.get(field_name)
        if existing not in (None, "") and value not in (None, "") and value != existing:
            fields[field_name] = f"{existing}\n{value}"
        elif field_name not in fields or existing in (None, ""):
            fields[field_name] = value
    return indexed


def mark_duplicate_results(
    results: list[dict[str, Any]], *, threshold: float = 0.8
) -> list[dict[str, Any]]:
    """Mark later duplicate records under the documented date/customer/title rule."""

    if not 0 < threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        result.setdefault("is_duplicate", False)
        result.setdefault("duplicate_similarity", 0.0)
        result.setdefault("duplicate_group", "")
        row = result["row"]
        create_dt = row.get("create_dt")
        company_key = _normalize(row.get("company_name") or "")
        title_key = _normalize(row.get("subject") or "")
        if not create_dt or not company_key or not title_key:
            continue
        groups[(create_dt.strftime("%Y-%m-%d"), company_key, title_key)].append(result)

    minimum_description_length = 10
    for (date_key, company_key, title_key), group in groups.items():
        for index, current in enumerate(group):
            if current["is_duplicate"]:
                continue
            current_description = (current["row"].get("problem_desc") or "").strip()
            normalized_current = _normalize(current_description)
            if len(normalized_current) < minimum_description_length:
                continue
            for candidate in group[index + 1 :]:
                if candidate["is_duplicate"]:
                    continue
                candidate_description = (candidate["row"].get("problem_desc") or "").strip()
                normalized_candidate = _normalize(candidate_description)
                if len(normalized_candidate) < minimum_description_length:
                    continue
                only_numbers_differ = (
                    _strip_numbers(normalized_current) == _strip_numbers(normalized_candidate)
                    and normalized_current != normalized_candidate
                )
                if only_numbers_differ:
                    continue
                similarity = _similarity(normalized_current, normalized_candidate)
                if similarity < threshold:
                    continue
                group_name = f"{date_key}_{company_key}_{title_key}_G{index + 1}"
                candidate["is_duplicate"] = True
                candidate["duplicate_similarity"] = similarity
                candidate["duplicate_group"] = group_name
                current["duplicate_group"] = current["duplicate_group"] or group_name

    return results


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 datetime: {value}") from exc


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _normalize(value: str) -> str:
    return re.sub(
        r"[\s\-_，。、；：！？\"'（）()【】\[\]{}《》<>·`~!@#$%^&*+=|\\\\/?]+",
        "",
        value.strip().lower(),
    )


def _strip_numbers(value: str) -> str:
    return re.sub(r"[0-9]+", "", value)


def _similarity(first: str, second: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, first, second).ratio()
