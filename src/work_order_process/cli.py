"""命令行入口。

支持的命令：
- run: 按年份导出月度工单合集和每月样本详情（三段式 JSON）
- template-samples: 按工单模板分别抽样
- mysql-init: 初始化 MySQL 5 表结构（含分区）
- mysql-drop-tables: 删除全部 5 张表（危险）
- mysql-import-ticket: 单条工单详情入库
- mysql-import-month: 某个月全部工单详情入库
- mysql-import-year: 某年全部工单详情入库（支持断点续跑）
- mysql-import-customers: 导入客户/公司到 customers 表
- mysql-import-contacts: 导入联系人到 contacts 表
- mysql-add-partitions: 提前创建未来的月分区
- mysql-sync-log: 查看同步任务日志
- probe: 探测接口可用性
- dictionary: 导出数据字典 JSON
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .api import ApiError, WorkOrderClient
from .config import ConfigError, PROJECT_ROOT, load_settings
from .dictionary import DataDictionary
from .mysql_storage import (
    add_future_partitions,
    create_customer_contact_analysis_views,
    drop_mysql_tables,
    ensure_mysql_schema,
    generate_months_ahead,
    get_existing_partitions,
    import_contacts_to_mysql,
    import_customers_to_mysql,
    import_ticket_detail_to_mysql,
    import_month_tickets_serial,
    import_month_tickets_to_mysql,
    import_year_tickets_to_mysql,
)
from .monthly_export import (
    export_month_template_samples,
    export_year_monthly_tickets,
    export_year_monthly_tickets_and_samples,
)
from .erp_import import import_erp_xlsx
from .customer_account_import import import_customer_account_xlsx
from .personnel_import import import_personnel_xls_to_mysql
from .revenue_summary import generate_revenue_summary
from .time_metrics import (
    DEFAULT_CALENDAR_PATH,
    DEFAULT_METRICS_CONFIG,
    export_month_time_metrics,
    export_ticket_time_metrics,
)


console = Console()


def main() -> None:
    """解析命令行参数并执行对应的工单处理流程。"""

    parser = argparse.ArgumentParser(description="工单数据获取、解析和入库工具。")
    parser.add_argument(
        "command",
        choices=[
            "run", "monthly-tickets", "template-samples",
            "mysql-init", "mysql-drop-tables", "mysql-create-analysis-views",
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
            "mysql-import-ticket: 单条入库；mysql-import-month: 单月入库；"
            "mysql-import-year: 全年入库；mysql-import-customers: 导入客户；"
            "mysql-import-contacts: 导入联系人；mysql-probe-customers/mysql-probe-contacts: 只读探测；mysql-add-partitions: 增加分区；"
            "mysql-sync-log: 查看同步日志；import-erp: ERP新旧数据Excel入库；"
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
        default=str(PROJECT_ROOT / "人员信息名单20260708.xls"),
        help="mysql-import-personnel: personnel .xls file path.",
    )
    parser.add_argument(
        "--customers-source",
        choices=["companies", "customers", "both"],
        default="companies",
        help="客户导入的数据源，默认 both。",
    )
    parser.add_argument(
        "--contacts-source",
        choices=["contacts", "company_contacts", "both"],
        default="contacts",
        help="联系人导入的数据源，默认 both。",
    )
    parser.add_argument(
        "--months-ahead",
        type=int,
        default=6,
        help="mysql-add-partitions: 提前创建多少个月的分区，默认 6。",
    )
    parser.add_argument(
        "--log-limit",
        type=int,
        default=20,
        help="mysql-sync-log: 显示最近多少条日志，默认 20。",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="允许客户或联系人接口返回 0 条时仍将同步批次标记为成功。默认禁止。",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="客户/联系人同步最多写入的记录数；用于受控验证，默认不限制。",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="并发导入时的 API 拉取线程数，默认 8。",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="并发导入时每批提交的事务大小，默认 100。",
    )
    parser.add_argument(
        "--api-rate-limit",
        type=int,
        default=10,
        help="并发导入时 API QPS 上限，默认 10。",
    )
    parser.add_argument(
        "--erp-file",
        default=None,
        help="import-erp: ERP新旧合并数据 Excel 文件路径。",
    )
    parser.add_argument(
        "--customer-account-file",
        default=None,
        help="import-customer-account: 客户台账明细 Excel 文件路径。",
    )
    parser.add_argument(
        "--revenue-target-file",
        default=None,
        help="generate-revenue-summary: 含固定收入目标值的月度 Excel 模板路径。",
    )
    parser.add_argument(
        "--erp-create-date",
        default=None,
        help="generate-revenue-summary: ERP 快照日期，如 20260717；默认取最新快照。",
    )
    parser.add_argument(
        "--revenue-output",
        default=None,
        help="generate-revenue-summary: 可选的统计结果 Excel 输出路径。",
    )
    parser.add_argument(
        "--revenue-preview",
        action="store_true",
        help="generate-revenue-summary: 仅生成 Excel 预览，不写入月度营收统计表。",
    )
    parser.add_argument(
        "--create-date",
        default=None,
        help="import-customer-account: 数据日期，如 20260710。",
    )
    parser.add_argument(
        "--sheet",
        default=None,
        help="import-customer-account: Sheet 名称（默认第一个）。",
    )
    args = parser.parse_args()

    settings = load_settings()
    dictionary = DataDictionary.from_pdf(settings.dictionary_path)

    if args.command == "dictionary":
        output = settings.output_dir / "dictionary.json"
        dictionary.save_json(output)
        console.print(f"数据字典已保存到 {output}")
        _print_dictionary_summary(dictionary)
        return

    if args.command == "mysql-init":
        ensure_mysql_schema(settings.mysql)
        partitions = get_existing_partitions(settings.mysql)
        month_count = len(partitions) - (1 if "pmax" in partitions else 0)
        console.print(
            f"[green]MySQL 数据库初始化完成[/green]\n"
            f"地址: {settings.mysql.host}:{settings.mysql.port}/{settings.mysql.database}\n"
            f"已创建 5 张表，{month_count} 个月分区 + pmax"
        )
        return

    if args.command == "mysql-create-analysis-views":
        create_customer_contact_analysis_views(settings.mysql)
        console.print("[green]客户/联系人分析视图已创建。[/green]")
        return

    if args.command == "mysql-drop-tables":
        drop_mysql_tables(settings.mysql)
        console.print("[yellow]全部 5 张表已删除。[/yellow]")
        return

    if args.command == "mysql-import-personnel":
        report = import_personnel_xls_to_mysql(settings.mysql, Path(args.personnel_file))
        _print_personnel_import_report(report)
        return

    if args.command == "import-erp":
        if not args.erp_file:
            raise ApiError("import-erp 需要传入 --erp-file。")
        report = import_erp_xlsx(settings.mysql, Path(args.erp_file))
        _print_erp_import_report(report)
        return

    if args.command == "generate-revenue-summary":
        if args.month is None:
            raise ApiError("generate-revenue-summary 需要传入 --month。")
        if not args.revenue_target_file:
            raise ApiError("generate-revenue-summary 需要传入 --revenue-target-file。")
        report = generate_revenue_summary(
            settings.mysql,
            target_file=Path(args.revenue_target_file),
            year=args.year,
            month=args.month,
            erp_create_date=args.erp_create_date,
            output_dir=settings.output_dir,
            output_path=Path(args.revenue_output) if args.revenue_output else None,
            persist=not args.revenue_preview,
        )
        _print_revenue_summary_report(report)
        return

    if args.command == "import-customer-account":
        if not args.customer_account_file:
            raise ApiError("import-customer-account 需要传入 --customer-account-file。")
        if not args.create_date:
            raise ApiError("import-customer-account 需要传入 --create-date。")
        report = import_customer_account_xlsx(
            settings.mysql, Path(args.customer_account_file), args.create_date, args.sheet,
        )
        _print_customer_account_import_report(report)
        return

    if args.command == "mysql-add-partitions":
        months_list = generate_months_ahead(args.months_ahead)
        created = add_future_partitions(settings.mysql, months_list)
        if created:
            console.print(f"[green]新建分区: {', '.join(created)}[/green]")
        else:
            console.print("[dim]所有月份分区均已存在，无需新建。[/dim]")
        return

    if args.command == "metric-month":
        if args.month is None:
            raise ApiError("metric-month requires --month.")
        report = export_month_time_metrics(
            settings.mysql,
            year=args.year,
            month=args.month,
            output_dir=settings.output_dir,
            metrics_config_path=Path(args.metrics_config),
            calendar_path=Path(args.calendar_path),
            metric_code=args.metric_code,
            limit=args.limit_per_month,
            output_path=Path(args.output) if args.output else None,
        )
        _print_time_metric_report(report)
        return

    if args.command == "metric-ticket":
        if not args.ticket_id:
            raise ApiError("metric-ticket requires --ticket-id.")
        report = export_ticket_time_metrics(
            settings.mysql,
            ticket_id=args.ticket_id,
            output_dir=settings.output_dir,
            metrics_config_path=Path(args.metrics_config),
            calendar_path=Path(args.calendar_path),
            metric_code=args.metric_code,
            output_path=Path(args.output) if args.output else None,
        )
        _print_time_metric_report(report)
        return

    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()

            if args.command == "mysql-import-ticket":
                if not args.ticket_id:
                    raise ApiError("Please pass --ticket-id for mysql-import-ticket.")
                report = import_ticket_detail_to_mysql(settings.mysql, dictionary, client, args.ticket_id)
                _print_mysql_import_report(report)
                return

            if args.command == "mysql-import-month":
                if args.month is None:
                    raise ApiError("mysql-import-month 需要传入 --month。")
                report = import_month_tickets_to_mysql(
                    settings.mysql, dictionary, client,
                    year=args.year, month=args.month, per_page=args.per_page,
                    limit_per_month=args.limit_per_month,
                    max_workers=args.max_workers, batch_size=args.batch_size,
                    api_rate_limit=args.api_rate_limit,
                )
                _print_mysql_month_report(report)
                return

            if args.command == "mysql-import-month-v1":
                # 保留旧的串行导入方式，用于调试对比
                if args.month is None:
                    raise ApiError("mysql-import-month-v1 需要传入 --month。")
                report = import_month_tickets_serial(
                    settings.mysql, dictionary, client,
                    year=args.year, month=args.month, per_page=args.per_page,
                    limit_per_month=args.limit_per_month,
                    output_dir=settings.output_dir,
                )
                _print_mysql_month_report(report)
                return

            if args.command == "mysql-import-year":
                report = import_year_tickets_to_mysql(
                    settings.mysql, dictionary, client,
                    year=args.year,
                    months=[args.month] if args.month is not None else None,
                    per_page=args.per_page,
                    limit_per_month=args.limit_per_month,
                    max_workers=args.max_workers, batch_size=args.batch_size,
                    api_rate_limit=args.api_rate_limit,
                    output_dir=settings.output_dir,
                )
                _print_mysql_year_report(report)
                return

            if args.command == "mysql-import-customers":
                sources = _resolve_sources(args.customers_source, ["companies", "customers"])
                report = import_customers_to_mysql(
                    settings.mysql, client, sources=sources, require_nonempty=not args.allow_empty,
                    max_records=args.max_records,
                )
                _print_customer_contact_report("customers", report)
                return

            if args.command == "mysql-import-contacts":
                sources = _resolve_sources(args.contacts_source, ["contacts", "company_contacts"])
                report = import_contacts_to_mysql(
                    settings.mysql, client, sources=sources, require_nonempty=not args.allow_empty,
                    max_records=args.max_records,
                )
                _print_customer_contact_report("contacts", report)
                return

            if args.command == "mysql-probe-customers":
                _print_entity_probe(client.probe_entity_paths(settings.endpoint.customer_paths, "customer", args.sample_size))
                return

            if args.command == "mysql-probe-contacts":
                _print_entity_probe(client.probe_entity_paths(settings.endpoint.contact_paths, "contact", args.sample_size))
                return

            if args.command == "mysql-sync-log":
                _print_sync_log(settings)
                return

            if args.command == "probe":
                _probe(client)
                return

            if args.command == "template-samples":
                if args.month is None:
                    raise ApiError("Please pass --month for template-samples.")
                report = export_month_template_samples(
                    settings.output_dir,
                    dictionary,
                    client,
                    year=args.year,
                    month=args.month,
                    sample_size=args.sample_size,
                    seed=args.seed,
                    overwrite=args.overwrite,
                    detail_workers=args.detail_workers,
                )
                _print_template_sample_report(report)
                return

            if args.command == "monthly-tickets":
                report = export_year_monthly_tickets(
                    settings.output_dir,
                    client,
                    year=args.year,
                    months=[args.month] if args.month is not None else None,
                    per_page=args.per_page,
                    limit_per_month=args.limit_per_month,
                    overwrite=args.overwrite,
                )
                _print_monthly_ticket_report(report)
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
                detail_workers=args.detail_workers,
            )
            _print_year_report(report)
    except ApiError as exc:
        console.print(f"[red]接口错误：[/red] {exc}")
        raise SystemExit(2) from exc
    except ConfigError as exc:
        console.print(f"[red]配置错误:[/red] {exc}")
        raise SystemExit(3) from exc


def _resolve_sources(source_arg: str, both: list[str]) -> tuple[str, ...]:
    """把 CLI 的 xxx-source 选项转换为来源元组。"""

    if source_arg == "both":
        return tuple(both)
    return (source_arg,)


def _print_sync_log(settings: Any) -> None:
    """读取 sync_task_log 并打印最近 N 条。"""

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
    """输 ERP 导入摘要。"""
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
    """输出运维服务月度营收统计摘要。"""

    table = Table("Field", "Value")
    table.add_row("Statistics period", f"{report['stat_year']}-{int(report['stat_month']):02d}")
    table.add_row("ERP snapshot", str(report["erp_create_date"]))
    table.add_row("Target platforms", str(report["target_platform_count"]))
    table.add_row("Summary rows", str(report["rows"]))
    table.add_row("Unmapped ERP platforms", ", ".join(report["unmapped_metric_platforms"]) or "None")
    table.add_row("Output", str(report["output_path"]))
    console.print(table)


def _print_customer_account_import_report(report: dict[str, Any]) -> None:
    """输出客户台账导入摘要。"""
    table = Table("Metric", "Value")
    table.add_row("File", report["file"])
    table.add_row("Rows", str(report["rows"]))
    table.add_row("Inserted", str(report["inserted"]))
    table.add_row("Skipped", str(report["skipped"]))
    table.add_row("Cleaned", str(report["cleaned"]))
    table.add_row("Duration (s)", str(report["seconds"]))
    console.print(table)


def _print_personnel_import_report(report: dict[str, Any]) -> None:
    """Print local personnel import summary."""

    table = Table("Table", "Source", "Rows", "Affected")
    table.add_row(
        str(report["table"]),
        str(report["source_file"]),
        str(report["total_count"]),
        str(report["affected_rows"]),
    )
    console.print(table)


def _get_log_limit() -> int:
    """返回 --log-limit 的值（延迟读取，避免全局 argparse 依赖）。"""

    import sys
    for idx, arg in enumerate(sys.argv):
        if arg == "--log-limit" and idx + 1 < len(sys.argv):
            return int(sys.argv[idx + 1])
    return 20


def _probe(client: WorkOrderClient) -> None:
    """探测当前 Basic Auth 和工单接口是否可访问。"""

    table = Table("Item", "OK", "Detail")
    for result in client.probe_auth_paths():
        table.add_row(result.path, "yes" if result.ok else "no", result.detail[:100])
    for result in client.probe_paths(client.settings.endpoint.ticket_paths[:1]):
        table.add_row(result.path, "yes" if result.ok else "no", result.detail[:100])
    console.print(table)


def _print_entity_probe(reports: list[dict[str, Any]]) -> None:
    """Print endpoint counts and keys only; never print personal-data values."""

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
    console.print(f"月度工单合集: {report['monthly_ticket_dir']}")
    console.print(f"月度样本详情: {report['monthly_sample_detail_dir']}")


def _print_monthly_ticket_report(report: dict[str, Any]) -> None:
    """输出只导出月度工单合集时的摘要。"""

    table = Table("Month", "Tickets", "Declared")
    for item in report["months"]:
        table.add_row(str(item["month"]), str(item["fetched_count"]), str(item["declared_count"]))
    table.add_row("total", str(report["ticket_total"]), "")
    console.print(table)
    console.print(f"月度工单合集: {report['monthly_ticket_dir']}")


def _print_template_sample_report(report: dict[str, Any]) -> None:
    """输出按模板抽样的摘要。"""

    table = Table("Template ID", "Template Name", "Month Count", "Sample")
    for item in report["templates"]:
        table.add_row(
            str(item["template_id"]),
            str(item["template_name"]),
            str(item["month_count"]),
            str(item["sample_count"]),
        )
    table.add_row("total", str(report["template_count"]), "", str(report["detail_count"]))
    console.print(table)
    console.print(f"模板样本详情: {report['output_dir']}")


def _print_mysql_import_report(report: dict[str, Any]) -> None:
    """输出 MySQL 单条工单入库摘要。"""

    table = Table("Metric", "Value")
    for key, value in report.items():
        table.add_row(str(key), str(value))
    console.print(table)


def _print_mysql_month_report(report: dict[str, Any]) -> None:
    """输出月度 MySQL 入库摘要。"""

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
    """输出年度 MySQL 入库摘要。"""

    table = Table("Month", "Total", "Imported", "Updated", "Skipped", "Failed")
    for item in report["months"]:
        table.add_row(
            item["month"],
            str(item["total_in_month"]),
            str(item["imported"]),
            str(item.get("updated", 0)),
            str(item.get("skipped", 0)),
            str(item["failed"]),
        )
    table.add_row(
        "total", "",
        str(report["total_imported"]),
        str(report.get("total_updated", 0)),
        str(report.get("total_skipped", 0)),
        str(report["total_failed"]),
    )
    console.print(table)


def _print_customer_contact_report(table_name: str, report: dict[str, Any]) -> None:
    """输出客户/联系人导入摘要。"""

    table = Table("Table", "Total", "Succeeded", "Failed", "Duration (s)")
    table.add_row(table_name, str(report["total"]), str(report["succeeded"]), str(report["failed"]), str(report.get("duration_seconds", "")))
    console.print(table)


def _print_time_metric_report(report: dict[str, Any]) -> None:
    """输出时间指标 JSON 导出摘要。"""

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
    """输出各张数据字典表解析到的字段数量。"""

    table = Table("Table", "Fields")
    for name, fields in dictionary.tables.items():
        table.add_row(name, str(len(fields)))
    console.print(table)
