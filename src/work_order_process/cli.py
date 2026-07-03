"""命令行入口。

当前项目只保留一个主要输出流程：
按 2025 年 1-12 月分别导出工单合集，并从每个月抽 3 条工单详情，
生成 raw、value_resolved、chinese 三份对照 JSON。
"""

from __future__ import annotations

import argparse
from typing import Any

from rich.console import Console
from rich.table import Table

from .api import ApiError, WorkOrderClient
from .config import load_settings
from .dictionary import DataDictionary
from .monthly_export import export_year_monthly_tickets_and_samples


console = Console()


def main() -> None:
    """解析命令行参数并执行当前保留的工单月度导出流程。"""

    parser = argparse.ArgumentParser(description="Export 2025 monthly work-order data.")
    parser.add_argument(
        "command",
        choices=["run", "probe", "dictionary"],
        nargs="?",
        default="run",
        help="run: 导出月度工单合集和每月 3 条详情；probe: 探测认证和接口；dictionary: 导出字段字典。",
    )
    parser.add_argument("--year", type=int, default=2025, help="需要导出的年份，默认 2025。")
    parser.add_argument("--month", type=int, default=None, help="只导出指定月份，取值 1-12；默认导出全年。")
    parser.add_argument("--sample-size", type=int, default=3, help="每个月抽取的工单详情数量，默认 3。")
    parser.add_argument("--seed", type=int, default=2025, help="月度抽样随机种子，默认 2025，保证可复现。")
    parser.add_argument("--per-page", type=int, default=5000, help="搜索接口分页大小，默认 5000。")
    parser.add_argument("--limit-per-month", type=int, default=None, help="调试用：限制每个月最多获取多少条列表记录。")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有月度输出文件。")
    args = parser.parse_args()

    settings = load_settings()
    dictionary = DataDictionary.from_pdf(settings.dictionary_path)

    if args.command == "dictionary":
        output = settings.output_dir / "dictionary.json"
        dictionary.save_json(output)
        console.print(f"Saved dictionary to {output}")
        _print_dictionary_summary(dictionary)
        return

    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()
            if args.command == "probe":
                _probe(client)
                return
            report = export_year_monthly_tickets_and_samples(
                settings.output_dir,
                dictionary,
                client,
                year=args.year,
                months=[args.month] if args.month is not None else None,
                sample_size=args.sample_size,
                seed=args.seed,
                per_page=args.per_page,
                limit_per_month=args.limit_per_month,
                overwrite=args.overwrite,
            )
            _print_year_report(report)
    except ApiError as exc:
        console.print(f"[red]API error:[/red] {exc}")
        raise SystemExit(2) from exc


def _probe(client: WorkOrderClient) -> None:
    """探测当前 Basic Auth 和工单接口是否可访问。"""

    table = Table("Item", "OK", "Detail")
    for result in client.probe_auth_paths():
        table.add_row(result.path, "yes" if result.ok else "no", result.detail[:100])
    for result in client.probe_paths(client.settings.endpoint.ticket_paths[:1]):
        table.add_row(result.path, "yes" if result.ok else "no", result.detail[:100])
    console.print(table)


def _print_year_report(report: dict[str, Any]) -> None:
    """输出年度月度导出摘要。"""

    table = Table("Month", "Tickets", "Sample Details", "Failed")
    for item in report["months"]:
        table.add_row(
            str(item["month"]),
            str(item["fetched_count"]),
            str(item["detail_count"]),
            str(item["failed_count"]),
        )
    table.add_row("total", str(report["ticket_total"]), str(report["detail_total"]), str(report["failed_total"]))
    console.print(table)
    console.print(f"Monthly tickets: {report['monthly_ticket_dir']}")
    console.print(f"Monthly sample details: {report['monthly_sample_detail_dir']}")


def _print_dictionary_summary(dictionary: DataDictionary) -> None:
    """输出各张数据字典表解析到的字段数量。"""

    table = Table("Table", "Fields")
    for name, fields in dictionary.tables.items():
        table.add_row(name, str(len(fields)))
    console.print(table)
