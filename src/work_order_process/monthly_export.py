"""2025 年工单月度合集与月度样本详情导出。

当前保留的业务目标很明确：
1. 按创建月份导出 2025 年每个月的工单列表合集；
2. 从每个月的合集里抽 3 条工单；
3. 对这 3 条详情按现有规则生成 raw、value_resolved、chinese 三份 JSON。
"""

from __future__ import annotations

import random
import json
from pathlib import Path
from typing import Any, Iterable

from .api import ApiError, WorkOrderClient
from .dictionary import DataDictionary
from .io import write_json
from .resolver import TicketFieldResolver, resolve_ticket_detail_values


MONTHLY_TICKET_DIR_TEMPLATE = "{year}_monthly_tickets"
MONTHLY_SAMPLE_DETAIL_DIR_TEMPLATE = "{year}_monthly_sample_details"


def export_year_monthly_tickets_and_samples(
    output_dir: Path,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int = 2025,
    months: Iterable[int] | None = None,
    sample_size: int = 3,
    seed: int = 2025,
    per_page: int = 5000,
    limit_per_month: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """导出某一年 12 个月的工单合集，并为每个月抽样生成详情三件套。

    `limit_per_month` 仅用于调试小样本，正式导出不要传它。抽样使用固定 seed，
    这样多次运行可以得到一致的 3 条样本，方便对照检查。
    """

    if sample_size < 1:
        raise ApiError("sample_size must be greater than 0.")

    monthly_ticket_dir = output_dir / MONTHLY_TICKET_DIR_TEMPLATE.format(year=year)
    monthly_sample_detail_dir = output_dir / MONTHLY_SAMPLE_DETAIL_DIR_TEMPLATE.format(year=year)
    field_resolver = TicketFieldResolver(client.fetch_ticket_fields(), client.fetch_company_fields())

    month_numbers = list(months) if months is not None else list(range(1, 13))
    month_reports: list[dict[str, Any]] = []
    for month in month_numbers:
        month_label = build_month_label(year, month)
        ticket_report = _load_or_fetch_month_tickets(
            output_dir,
            client,
            year,
            month,
            per_page=per_page,
            limit_per_month=limit_per_month,
            overwrite=overwrite,
        )
        sample_rows = _sample_ticket_rows(ticket_report["tickets"], sample_size, seed, month_label)
        detail_report = _export_month_sample_details(
            monthly_sample_detail_dir,
            month_label,
            sample_rows,
            dictionary,
            client,
            field_resolver,
            overwrite=overwrite or bool(ticket_report.get("_regenerated")),
        )
        month_reports.append(
            {
                "month": month_label,
                "declared_count": ticket_report["declared_count"],
                "fetched_count": ticket_report["fetched_count"],
                "ticket_output": str(monthly_ticket_dir / f"{month_label}_tickets.json"),
                **detail_report,
            }
        )

    return {
        "year": year,
        "ticket_total": sum(item["fetched_count"] for item in month_reports),
        "detail_total": sum(item["detail_count"] for item in month_reports),
        "failed_total": sum(item["failed_count"] for item in month_reports),
        "monthly_ticket_dir": str(monthly_ticket_dir),
        "monthly_sample_detail_dir": str(monthly_sample_detail_dir),
        "months": month_reports,
    }


def build_month_label(year: int, month: int) -> str:
    """把年、月格式化为接口搜索需要的 `YYYY-MM`。"""

    if month < 1 or month > 12:
        raise ApiError("Month must be between 1 and 12.")
    return f"{year}-{month:02d}"


def fetch_month_ticket_rows(
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 5000,
    limit_per_month: int | None = None,
) -> dict[str, Any]:
    """通过搜索接口分页获取某个月的工单合集。"""

    month_label = build_month_label(year, month)
    if per_page < 1:
        raise ApiError("per_page must be greater than 0.")
    if limit_per_month is not None and limit_per_month < 1:
        raise ApiError("limit_per_month must be greater than 0.")

    rows: list[dict[str, Any]] = []
    declared_count = 0
    page = 1
    while True:
        tickets = client.search_tickets_by_create_month(month_label, page=page, per_page=per_page)
        if page == 1:
            declared_count = _safe_int(tickets.get("count"))
        page_rows = _extract_search_rows(tickets.get("results"))
        if not page_rows:
            break
        rows.extend(page_rows)
        if limit_per_month is not None and len(rows) >= limit_per_month:
            rows = rows[:limit_per_month]
            break
        if len(rows) >= declared_count:
            break
        page += 1

    return {
        "month": month_label,
        "declared_count": declared_count,
        "fetched_count": len(rows),
        "ticket_ids": _ticket_ids_from_rows(rows),
        "tickets": rows,
        "limit_per_month": limit_per_month,
    }


def _load_or_fetch_month_tickets(
    output_dir: Path,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int,
    limit_per_month: int | None,
    overwrite: bool,
) -> dict[str, Any]:
    """读取已有月度合集，或调用接口重新生成。"""

    month_label = build_month_label(year, month)
    output_path = _month_ticket_path(output_dir, year, month_label)
    if output_path.exists() and not overwrite:
        data = _load_json_object(output_path)
        if _is_partial_report(data) and limit_per_month is None:
            report = fetch_month_ticket_rows(client, year, month, per_page=per_page, limit_per_month=None)
            report["_regenerated"] = True
            write_json(output_path, report)
            return report
        return _slice_month_report(data, limit_per_month)

    report = fetch_month_ticket_rows(client, year, month, per_page=per_page, limit_per_month=limit_per_month)
    write_json(output_path, report)
    return report


def _export_month_sample_details(
    output_dir: Path,
    month_label: str,
    sample_rows: list[dict[str, Any]],
    dictionary: DataDictionary,
    client: WorkOrderClient,
    field_resolver: TicketFieldResolver,
    overwrite: bool,
) -> dict[str, Any]:
    """把某个月抽到的工单详情输出成三份 JSON。"""

    raw_path = output_dir / f"{month_label}_sample_details_raw.json"
    value_path = output_dir / f"{month_label}_sample_details_value_resolved.json"
    chinese_path = output_dir / f"{month_label}_sample_details_chinese.json"
    existing = [path for path in (raw_path, value_path, chinese_path) if path.exists()]
    if len(existing) == 3 and not overwrite:
        return {
            "sample_ticket_ids": [str(row.get("ticketId")) for row in sample_rows if row.get("ticketId")],
            "detail_count": _json_array_len(raw_path),
            "failed_count": 0,
            "failed_ids": [],
            "raw_output": str(raw_path),
            "value_resolved_output": str(value_path),
            "chinese_output": str(chinese_path),
        }
    if existing and not overwrite:
        raise ApiError(f"Incomplete sample detail output exists for {month_label}. Use --overwrite to regenerate it.")

    raw_details: list[dict[str, Any]] = []
    value_details: list[dict[str, Any]] = []
    chinese_details: list[dict[str, Any]] = []
    failed_ids: list[str] = []

    for row in sample_rows:
        ticket_id = str(row.get("ticketId") or "").strip()
        if not ticket_id:
            continue
        raw_detail = client.fetch_ticket_detail(ticket_id)
        if not raw_detail:
            failed_ids.append(ticket_id)
            continue
        value_resolved = resolve_ticket_detail_values(raw_detail, client, field_resolver)
        raw_details.append(raw_detail)
        value_details.append(value_resolved)
        chinese_details.append(dictionary.translate_record("tickets", value_resolved))

    write_json(raw_path, raw_details)
    write_json(value_path, value_details)
    write_json(chinese_path, chinese_details)
    return {
        "sample_ticket_ids": [str(row.get("ticketId")) for row in sample_rows if row.get("ticketId")],
        "detail_count": len(raw_details),
        "failed_count": len(failed_ids),
        "failed_ids": failed_ids,
        "raw_output": str(raw_path),
        "value_resolved_output": str(value_path),
        "chinese_output": str(chinese_path),
    }


def _sample_ticket_rows(rows: list[dict[str, Any]], sample_size: int, seed: int, month_label: str) -> list[dict[str, Any]]:
    """从月度工单合集里按固定种子抽样。"""

    if len(rows) <= sample_size:
        return list(rows)
    rng = random.Random(f"{seed}:{month_label}")
    return rng.sample(rows, sample_size)


def _month_ticket_path(output_dir: Path, year: int, month_label: str) -> Path:
    """生成某个月工单合集文件路径。"""

    return output_dir / MONTHLY_TICKET_DIR_TEMPLATE.format(year=year) / f"{month_label}_tickets.json"


def _load_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象文件。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ApiError(f"Expected JSON object: {path}")
    return data


def _is_partial_report(report: dict[str, Any]) -> bool:
    """判断月度合集是否只是调试样本。"""

    declared_count = _safe_int(report.get("declared_count"))
    fetched_count = _safe_int(report.get("fetched_count"))
    return declared_count > 0 and fetched_count > 0 and fetched_count < declared_count


def _slice_month_report(report: dict[str, Any], limit_per_month: int | None) -> dict[str, Any]:
    """调试时从已有月度合集里截取前 N 条，不修改磁盘文件。"""

    if limit_per_month is None:
        return report
    sliced = dict(report)
    tickets = _extract_search_rows(report.get("tickets"))
    sliced["tickets"] = tickets[:limit_per_month]
    sliced["ticket_ids"] = _ticket_ids_from_rows(sliced["tickets"])
    sliced["fetched_count"] = len(sliced["tickets"])
    sliced["limit_per_month"] = limit_per_month
    return sliced


def _extract_search_rows(results: Any) -> list[dict[str, Any]]:
    """从搜索接口 results 字段中提取工单列表行。"""

    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _ticket_ids_from_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    """从工单列表行里提取 ticketId，并按原顺序去重。"""

    seen: set[str] = set()
    ticket_ids: list[str] = []
    for row in rows:
        ticket_id = str(row.get("ticketId") or "").strip()
        if not ticket_id or ticket_id in seen:
            continue
        seen.add(ticket_id)
        ticket_ids.append(ticket_id)
    return ticket_ids


def _safe_int(value: Any) -> int:
    """把接口返回的数字字符串安全转成 int。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_array_len(path: Path) -> int:
    """读取 JSON 数组长度，用于复用已有样本详情时汇总数量。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    return len(data) if isinstance(data, list) else 0
