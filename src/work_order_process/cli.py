"""Command-line entry point for work-order processing."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .api import ApiError, WorkOrderClient
from .config import ConfigError, Settings, load_settings
from .dictionary import DataDictionary
from .mysql_storage import (
    add_future_partitions,
    create_customer_contact_analysis_views,
    drop_mysql_tables,
    ensure_mysql_schema,
    generate_months_ahead,
    get_existing_partitions,
)
from .revenue_summary import generate_revenue_summary
from .schema_migrations import (
    assert_schema_current,
    migrate_schema,
    record_satisfied_schema,
    schema_status,
)
from .time_metrics import DEFAULT_CALENDAR_PATH, DEFAULT_METRICS_CONFIG


console = Console()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser without consuming process arguments."""

    parser = argparse.ArgumentParser(description="工单数据获取、解析和入库工具。")
    parser.add_argument(
        "command",
        choices=[
            "run", "monthly-tickets", "template-samples",
            "mysql-init", "mysql-schema-status", "mysql-migrate",
            "mysql-drop-tables", "mysql-create-analysis-views",
            "mysql-import-ticket", "mysql-import-month", "mysql-import-month-v1", "mysql-import-year",
            "mysql-import-customers", "mysql-import-contacts", "mysql-probe-customers", "mysql-probe-contacts",
            "mysql-import-personnel",
            "mysql-add-partitions", "mysql-sync-log",
            "import-erp", "import-customer-account",
            "generate-revenue-summary",
            "metric-month", "metric-ticket",
            "probe", "dictionary",
        ],
        nargs="?",
        default="run",
        help=(
            "run: 导出月度工单合集和样本详情；monthly-tickets: 只导出月度工单合集；template-samples: 按模板抽样；"
            "mysql-init: 初始化表结构；mysql-create-analysis-views: 创建分析视图；mysql-drop-tables: 删除全部表；"
            "mysql-import-ticket: 单条入库；mysql-import-month: 单月入库；mysql-import-year: 全年入库；"
            "mysql-import-customers: 导入客户；mysql-import-contacts: 导入联系人；"
            "mysql-probe-customers/mysql-probe-contacts: 只读探测；mysql-add-partitions: 增加分区；"
            "mysql-sync-log: 查看同步日志；import-erp: ERP 新旧数据 Excel 入库；"
            "probe: 探测接口；dictionary: 导出数据字典。"
        ),
    )
    parser.add_argument("--ticket-id", default=None, help="MySQL 入库时指定单条工单 ID。")
    parser.add_argument("--year", type=int, default=2025, help="需要处理的年份，默认 2025。")
    parser.add_argument("--month", type=int, default=None, help="只处理指定月份，取值 1-12；默认处理全年。")
    parser.add_argument("--sample-size", type=int, default=3, help="每个月抽取的工单详情数量，默认 3。")
    parser.add_argument("--seed", type=int, default=2025, help="月度抽样随机种子，默认 2025，保证可复现。")
    parser.add_argument("--per-page", type=int, default=5000, help="搜索接口分页大小，默认 5000。")
    parser.add_argument("--detail-workers", type=int, default=4, help="样本详情并发获取线程数，默认 4。")
    parser.add_argument("--limit-per-month", type=int, default=None, help="调试用：限制每个月最多获取多少条列表记录。")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有月度输出文件。")
    parser.add_argument("--metric-code", default=None, help="Only calculate one configured time metric.")
    parser.add_argument("--metrics-config", default=str(DEFAULT_METRICS_CONFIG), help="Time metric config JSON path.")
    parser.add_argument("--calendar-path", default=str(DEFAULT_CALENDAR_PATH), help="Work calendar JSON path.")
    parser.add_argument("--output", default=None, help="Output JSON path.")
    parser.add_argument(
        "--personnel-file",
        default=None,
        help="mysql-import-personnel: required personnel .xls file path.",
    )
    parser.add_argument(
        "--customers-source",
        choices=["companies", "customers", "both"],
        default="companies",
        help="客户导入的数据源，默认 companies。",
    )
    parser.add_argument(
        "--contacts-source",
        choices=["contacts", "company_contacts", "both"],
        default="contacts",
        help="联系人导入的数据源，默认 contacts。",
    )
    parser.add_argument("--months-ahead", type=int, default=6, help="mysql-add-partitions: 提前创建多少个月的分区，默认 6。")
    parser.add_argument("--log-limit", type=int, default=20, help="mysql-sync-log: 显示最近多少条日志，默认 20。")
    parser.add_argument("--allow-empty", action="store_true", help="允许客户或联系人接口返回 0 条时仍将同步批次标记为成功。默认禁止。")
    parser.add_argument("--max-records", type=int, default=None, help="客户/联系人同步最多写入的记录数；用于受控验证，默认不限制。")
    parser.add_argument("--max-workers", type=int, default=8, help="并发导入时的 API 拉取线程数，默认 8。")
    parser.add_argument("--batch-size", type=int, default=100, help="并发导入时每批提交的事务大小，默认 100。")
    parser.add_argument("--api-rate-limit", type=int, default=10, help="并发导入时 API QPS 上限，默认 10。")
    parser.add_argument("--erp-file", default=None, help="import-erp: ERP 新旧合并数据 Excel 文件路径。")
    parser.add_argument("--customer-account-file", default=None, help="import-customer-account: 客户台账明细 Excel 文件路径。")
    parser.add_argument("--revenue-target-file", default=None, help="generate-revenue-summary: 含固定收入目标值的月度 Excel 模板路径。")
    parser.add_argument("--erp-create-date", default=None, help="generate-revenue-summary: ERP 快照日期，如 20260717；默认取最新快照。")
    parser.add_argument("--revenue-output", default=None, help="generate-revenue-summary: 可选的统计结果 Excel 输出路径。")
    parser.add_argument("--revenue-preview", action="store_true", help="generate-revenue-summary: 仅生成 Excel 预览，不写入月度营收统计表。")
    parser.add_argument("--create-date", default=None, help="import-customer-account: 数据日期，如 20260710。")
    parser.add_argument("--sheet", default=None, help="import-customer-account: Sheet 名称（默认第一个）。")
    return parser


def dispatch_command(
    args: argparse.Namespace,
    settings: Settings,
    parser: argparse.ArgumentParser,
) -> None:
    """Dispatch a parsed command to the first matching domain handler."""

    from .cli_commands import database, diagnostics, exports, imports

    for handler in (database.handle, imports.handle, exports.handle, diagnostics.handle):
        if handler(args, settings, parser):
            return
    raise RuntimeError(f"No CLI handler accepted command: {args.command}")


def main() -> None:
    """Parse command-line arguments and execute the requested command."""

    parser = build_parser()
    args = parser.parse_args()
    settings = load_settings()
    dispatch_command(args, settings, parser)


def _resolve_sources(source_arg: str, both: list[str]) -> tuple[str, ...]:
    """Convert a ``--*-source`` option into source names."""

    if source_arg == "both":
        return tuple(both)
    return (source_arg,)


def _print_sync_log(settings: Any) -> None:
    """Read and print the latest sync-task log entries."""

    import pymysql

    limit = _get_log_limit()
    with pymysql.connect(
        host=settings.mysql.host,
        port=settings.mysql.port,
        user=settings.mysql.user,
        password=settings.mysql.password,
        database=settings.mysql.database,
        charset="utf8mb4",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, task_type, target_month_label, status, "
                "total_count, success_count, failed_count, skipped_count, "
                "duration_seconds, started_at, finished_at "
                "FROM sync_task_log ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()

    if not rows:
        console.print("[dim]sync_task_log 表为空。[/dim]")
        return

    table = Table("ID", "Type", "Month", "Status", "Total", "OK", "Fail", "Skip", "Secs")
    for row in rows:
        table.add_row(
            str(row[0]), row[1], row[2] or "-", row[3],
            str(row[4]), str(row[5]), str(row[6]), str(row[7]), str(row[8] or ""),
        )
    console.print(table)


def _print_erp_import_report(report: dict[str, Any]) -> None:
    table = Table("Metric", "Value")
    table.add_row("File", report["file"])
    table.add_row("Rows", str(report["rows"]))
    table.add_row("Inserted", str(report["inserted"]))
    if "updated" in report:
        table.add_row("Updated", str(report["updated"]))
    if "unchanged" in report:
        table.add_row("Unchanged", str(report["unchanged"]))
    table.add_row("Skipped", str(report["skipped"]))
    if "reused_baseline_sales_platform" in report:
        table.add_row("Reused baseline sales_platform", str(report["reused_baseline_sales_platform"]))
    if "new_sales_platform" in report:
        table.add_row("New-row Excel sales_platform", str(report["new_sales_platform"]))
    if "applied_system_engineer_mapping" in report:
        table.add_row("Applied system_engineer mapping", str(report["applied_system_engineer_mapping"]))
    if "kept_excel_system_engineer" in report:
        table.add_row("Kept Excel system_engineer", str(report["kept_excel_system_engineer"]))
    table.add_row("Duration (s)", str(report["seconds"]))
    console.print(table)


def _print_revenue_summary_report(report: dict[str, Any]) -> None:
    table = Table("Field", "Value")
    table.add_row("Statistics period", f"{report['stat_year']}-{int(report['stat_month']):02d}")
    table.add_row("ERP snapshot", str(report["erp_create_date"]))
    table.add_row("Target platforms", str(report["target_platform_count"]))
    table.add_row("Summary rows", str(report["rows"]))
    table.add_row("Unmapped ERP platforms", ", ".join(report["unmapped_metric_platforms"]) or "None")
    table.add_row("Output", str(report["output_path"]))
    console.print(table)


def _print_customer_account_import_report(report: dict[str, Any]) -> None:
    table = Table("Metric", "Value")
    table.add_row("File", report["file"])
    table.add_row("Rows", str(report["rows"]))
    table.add_row("Inserted", str(report["inserted"]))
    table.add_row("Skipped", str(report["skipped"]))
    table.add_row("Cleaned", str(report["cleaned"]))
    table.add_row("Duration (s)", str(report["seconds"]))
    console.print(table)


def _print_personnel_import_report(report: dict[str, Any]) -> None:
    table = Table("Table", "Source", "Rows", "Affected")
    table.add_row(str(report["table"]), str(report["source_file"]), str(report["total_count"]), str(report["affected_rows"]))
    console.print(table)


def _get_log_limit() -> int:
    import sys

    for idx, arg in enumerate(sys.argv):
        if arg == "--log-limit" and idx + 1 < len(sys.argv):
            return int(sys.argv[idx + 1])
    return 20


def _probe(client: WorkOrderClient) -> None:
    table = Table("Item", "OK", "Detail")
    for result in client.probe_auth_paths():
        table.add_row(result.path, "yes" if result.ok else "no", result.detail[:100])
    for result in client.probe_paths(client.settings.endpoint.ticket_paths[:1]):
        table.add_row(result.path, "yes" if result.ok else "no", result.detail[:100])
    console.print(table)


def _print_entity_probe(reports: list[dict[str, Any]]) -> None:
    table = Table("Path", "Entity", "Status", "Records", "Field Keys")
    for report in reports:
        table.add_row(
            str(report["path"]),
            str(report["entity_type"]),
            str(report["status"]),
            str(report.get("count", "-")),
            ", ".join(report.get("sample_keys", [])) or "-",
        )
    console.print(table)


def _print_year_report(report: dict[str, Any]) -> None:
    table = Table("Month", "Tickets", "Sample Details", "Failed")
    for item in report["months"]:
        table.add_row(str(item["month"]), str(item["fetched_count"]), str(item["detail_count"]), str(item["failed_count"]))
    table.add_row("total", str(report["ticket_total"]), str(report["detail_total"]), str(report["failed_total"]))
    console.print(table)
    console.print(f"月度工单合集: {report['monthly_ticket_dir']}")
    console.print(f"月度样本详情: {report['monthly_sample_detail_dir']}")


def _print_monthly_ticket_report(report: dict[str, Any]) -> None:
    table = Table("Month", "Tickets", "Declared")
    for item in report["months"]:
        table.add_row(str(item["month"]), str(item["fetched_count"]), str(item["declared_count"]))
    table.add_row("total", str(report["ticket_total"]), "")
    console.print(table)
    console.print(f"月度工单合集: {report['monthly_ticket_dir']}")


def _print_template_sample_report(report: dict[str, Any]) -> None:
    table = Table("Template ID", "Template Name", "Month Count", "Sample")
    for item in report["templates"]:
        table.add_row(str(item["template_id"]), str(item["template_name"]), str(item["month_count"]), str(item["sample_count"]))
    table.add_row("total", str(report["template_count"]), "", str(report["detail_count"]))
    console.print(table)
    console.print(f"模板样本详情: {report['output_dir']}")


def _print_mysql_import_report(report: dict[str, Any]) -> None:
    table = Table("Metric", "Value")
    for key, value in report.items():
        table.add_row(str(key), str(value))
    console.print(table)


def _print_mysql_month_report(report: dict[str, Any]) -> None:
    table = Table("Metric", "Value")
    table.add_row("Month", report["month"])
    table.add_row("Total in month", str(report["total_in_month"]))
    table.add_row("Imported", str(report["imported"]))
    table.add_row("Updated", str(report.get("updated", 0)))
    table.add_row("Skipped", str(report.get("skipped", 0)))
    table.add_row("Failed", str(report["failed"]))
    table.add_row("Custom field rows", str(report["custom_field_rows"]))
    table.add_row("Duration (s)", str(report.get("duration_seconds", "")))
    if report.get("failed_ids"):
        table.add_row("Failed IDs", ", ".join(str(x) for x in report["failed_ids"][:20]))
    console.print(table)


def _print_mysql_year_report(report: dict[str, Any]) -> None:
    table = Table("Month", "Total", "Imported", "Updated", "Skipped", "Failed")
    for item in report["months"]:
        table.add_row(item["month"], str(item["total_in_month"]), str(item["imported"]), str(item.get("updated", 0)), str(item.get("skipped", 0)), str(item["failed"]))
    table.add_row("total", "", str(report["total_imported"]), str(report.get("total_updated", 0)), str(report.get("total_skipped", 0)), str(report["total_failed"]))
    console.print(table)


def _print_customer_contact_report(table_name: str, report: dict[str, Any]) -> None:
    table = Table("Table", "Total", "Succeeded", "Failed", "Duration (s)")
    table.add_row(table_name, str(report["total"]), str(report["succeeded"]), str(report["failed"]), str(report.get("duration_seconds", "")))
    console.print(table)


def _print_time_metric_report(report: dict[str, Any]) -> None:
    table = Table("Metric", "Value")
    if report.get("month"):
        table.add_row("Month", str(report["month"]))
    if report.get("ticket_id"):
        table.add_row("Ticket ID", str(report["ticket_id"]))
    table.add_row("Tickets", str(report.get("ticket_count", 1)))
    table.add_row("Metric count", str(report["metric_count"]))
    table.add_row("Rows", str(report["result_count"]))
    table.add_row("Status counts", str(report["summary"]["status_counts"]))
    table.add_row("Output", str(report["output_path"]))
    console.print(table)


def _print_dictionary_summary(dictionary: DataDictionary) -> None:
    table = Table("Table", "Fields")
    for name, fields in dictionary.tables.items():
        table.add_row(name, str(len(fields)))
    console.print(table)
