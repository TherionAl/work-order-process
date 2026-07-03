"""按创建月份导出工单 ID 和工单详情。

本模块保留已经验证可用的月度查询方法：
`/tickets/search.json?query=createDT:YYYY-MM`。后续全量处理时先按月拿工单 ID，
再按 ID 调详情接口，避免直接一次性处理 2025 年全部工单造成文件过大或任务失控。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .api import ApiError, WorkOrderClient
from .dictionary import DataDictionary
from .io import write_json
from .resolver import TicketFieldResolver, resolve_ticket_detail_values


MONTHLY_ID_DIR = "monthly_ticket_ids"
MONTHLY_DETAIL_DIR = "monthly_ticket_details"


def build_month_label(year: int, month: int) -> str:
    """把年、月参数格式化为接口搜索需要的 `YYYY-MM`。"""

    if month < 1 or month > 12:
        raise ApiError("Month must be between 1 and 12.")
    return f"{year}-{month:02d}"


def count_ticket_months(client: WorkOrderClient, year: int) -> dict[str, Any]:
    """统计指定年份每个月的工单数量。

    搜索接口返回的 `tickets.count` 是字符串，这里统一转成整数，便于估算后续导出规模。
    """

    months: list[dict[str, Any]] = []
    total = 0
    for month in range(1, 13):
        month_label = build_month_label(year, month)
        tickets = client.search_tickets_by_create_month(month_label, page=1, per_page=1)
        count = _safe_int(tickets.get("count"))
        total += count
        months.append({"month": month_label, "count": count})
    return {"year": year, "total": total, "months": months}


def fetch_month_ticket_rows(
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 1000,
    limit: int | None = None,
) -> dict[str, Any]:
    """按创建月份分页获取工单列表行，重点保留 ticketId。

    返回值同时保存接口声明总量、实际抓取量和列表行。列表行中通常包含 ticketId、
    subject、createDT、updateDT、url 等字段，后续详情导出只依赖 ticketId。
    """

    month_label = build_month_label(year, month)
    if per_page < 1:
        raise ApiError("per_page must be greater than 0.")
    if limit is not None and limit < 1:
        raise ApiError("limit must be greater than 0.")

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
        if limit is not None and len(rows) >= limit:
            rows = rows[:limit]
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
    }


def export_month_ticket_ids(
    output_dir: Path,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 1000,
    limit: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """把指定月份的工单 ID 列表保存到本地 JSON。"""

    month_label = build_month_label(year, month)
    output_path = _month_ids_path(output_dir, month_label)
    if output_path.exists() and not overwrite:
        report = _load_id_report(output_path)
        if _is_partial_id_report(report) and limit is None:
            raise ApiError(
                "Existing monthly ticket id file is partial. "
                "Use --overwrite to regenerate the full month before exporting all details."
            )
        return _slice_id_report(report, limit)

    report = fetch_month_ticket_rows(client, year, month, per_page=per_page, limit=limit)
    report["limit"] = limit
    report["output"] = str(output_path)
    write_json(output_path, report)
    return report


def export_month_ticket_details(
    output_dir: Path,
    dictionary: DataDictionary,
    client: WorkOrderClient,
    year: int,
    month: int,
    per_page: int = 1000,
    limit: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """按月度工单 ID 逐条拉详情，并生成 raw、value_resolved、chinese 三份文件。

    详情文件采用流式写入 JSON 数组，避免单月几万条工单全部堆在内存里。
    如果本地已经有该月 ID 文件，会优先复用；传入 limit 时会按已有 ID 文件截取前 N 条。
    """

    month_label = build_month_label(year, month)
    raw_path = _month_detail_path(output_dir, month_label, "raw")
    value_path = _month_detail_path(output_dir, month_label, "value_resolved")
    chinese_path = _month_detail_path(output_dir, month_label, "chinese")
    existing_outputs = [path for path in (raw_path, value_path, chinese_path) if path.exists()]
    if existing_outputs and not overwrite:
        raise ApiError("Monthly detail output already exists. Use --overwrite to regenerate it.")

    id_report = export_month_ticket_ids(
        output_dir,
        client,
        year,
        month,
        per_page=per_page,
        limit=limit,
        overwrite=overwrite and limit is None,
    )
    ticket_ids = [ticket_id for ticket_id in id_report.get("ticket_ids", []) if str(ticket_id).strip()]
    if limit is not None:
        ticket_ids = ticket_ids[:limit]

    field_resolver = TicketFieldResolver(client.fetch_ticket_fields(), client.fetch_company_fields())
    success_count = 0
    failed_ids: list[str] = []

    with _JsonArrayWriter(raw_path) as raw_writer:
        with _JsonArrayWriter(value_path) as value_writer:
            with _JsonArrayWriter(chinese_path) as chinese_writer:
                for ticket_id in ticket_ids:
                    raw_detail = client.fetch_ticket_detail(str(ticket_id))
                    if not raw_detail:
                        failed_ids.append(str(ticket_id))
                        continue
                    value_resolved = resolve_ticket_detail_values(raw_detail, client, field_resolver)
                    chinese = dictionary.translate_record("tickets", value_resolved)
                    raw_writer.write(raw_detail)
                    value_writer.write(value_resolved)
                    chinese_writer.write(chinese)
                    success_count += 1

    return {
        "month": month_label,
        "declared_count": id_report.get("declared_count"),
        "id_count": len(ticket_ids),
        "detail_count": success_count,
        "failed_count": len(failed_ids),
        "failed_ids": failed_ids[:50],
        "id_output": id_report.get("output") or str(_month_ids_path(output_dir, month_label)),
        "raw_output": str(raw_path),
        "value_resolved_output": str(value_path),
        "chinese_output": str(chinese_path),
    }


def _month_ids_path(output_dir: Path, month_label: str) -> Path:
    """生成某月工单 ID 文件路径。"""

    return output_dir / MONTHLY_ID_DIR / f"{month_label}_ticket_ids.json"


def _month_detail_path(output_dir: Path, month_label: str, suffix: str) -> Path:
    """生成某月详情结果文件路径。"""

    return output_dir / MONTHLY_DETAIL_DIR / f"{month_label}_ticket_details_{suffix}.json"


def _load_id_report(path: Path) -> dict[str, Any]:
    """读取已有的月度工单 ID 文件。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ApiError(f"Monthly ticket id file is not a JSON object: {path}")
    data.setdefault("output", str(path))
    return data


def _is_partial_id_report(report: dict[str, Any]) -> bool:
    """判断已有 ID 文件是否只是 limit 样本，而不是完整月份。"""

    declared_count = _safe_int(report.get("declared_count"))
    fetched_count = _safe_int(report.get("fetched_count"))
    return declared_count > 0 and fetched_count > 0 and fetched_count < declared_count


def _slice_id_report(report: dict[str, Any], limit: int | None) -> dict[str, Any]:
    """按 limit 返回 ID 文件的视图，不修改磁盘上的原文件。"""

    if limit is None:
        return report
    sliced = dict(report)
    tickets = _extract_search_rows(sliced.get("tickets"))
    ticket_ids = [str(ticket_id) for ticket_id in sliced.get("ticket_ids", []) if str(ticket_id).strip()]
    sliced["tickets"] = tickets[:limit]
    sliced["ticket_ids"] = ticket_ids[:limit]
    sliced["fetched_count"] = len(sliced["ticket_ids"])
    sliced["limit"] = limit
    return sliced


def _extract_search_rows(results: Any) -> list[dict[str, Any]]:
    """从搜索接口 results 字段中提取工单列表行。"""

    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _ticket_ids_from_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    """从工单列表行中提取 ticketId，并去重保持原顺序。"""

    seen: set[str] = set()
    ids: list[str] = []
    for row in rows:
        ticket_id = str(row.get("ticketId") or "").strip()
        if not ticket_id or ticket_id in seen:
            continue
        seen.add(ticket_id)
        ids.append(ticket_id)
    return ids


def _safe_int(value: Any) -> int:
    """把接口里的数字字符串安全转成 int。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class _JsonArrayWriter:
    """流式写入 JSON 数组的小工具，适合单月大量详情导出。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None
        self._first = True

    def __enter__(self) -> "_JsonArrayWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        self._file.write("[\n")
        return self

    def __exit__(self, *_args: object) -> None:
        if self._file is None:
            return
        self._file.write("\n]\n")
        self._file.close()

    def write(self, item: Any) -> None:
        """向数组追加一条 JSON 记录。"""

        if self._file is None:
            raise ApiError("JSON writer is not open.")
        if not self._first:
            self._file.write(",\n")
        self._file.write(json.dumps(item, ensure_ascii=False, indent=2, default=str))
        self._first = False
