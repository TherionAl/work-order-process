"""Export and reporting CLI commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..api import ApiError, WorkOrderClient
from ..config import ConfigError, Settings
from ..dictionary import DataDictionary
from ..monthly_export import (
    export_month_template_samples,
    export_year_monthly_tickets,
    export_year_monthly_tickets_and_samples,
)
from ..time_metrics import export_month_time_metrics, export_ticket_time_metrics


COMMANDS = frozenset(
    {
        "run",
        "monthly-tickets",
        "template-samples",
        "generate-revenue-summary",
        "metric-month",
        "metric-ticket",
    }
)


def handle(args: argparse.Namespace, settings: Settings, parser: argparse.ArgumentParser) -> bool:
    """Handle exports, reports, and time metrics."""

    if args.command not in COMMANDS:
        return False

    from ..cli import (
        _print_monthly_ticket_report,
        _print_revenue_summary_report,
        _print_template_sample_report,
        _print_time_metric_report,
        _print_year_report,
        assert_schema_current,
        generate_revenue_summary,
    )

    if args.command == "generate-revenue-summary":
        assert_schema_current(settings.mysql)

    dictionary = DataDictionary.from_pdf(settings.dictionary_path)

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
        return True

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
        return True

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
        return True

    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()
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
            elif args.command == "monthly-tickets":
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
            else:
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
        from .. import cli

        cli.console.print(f"[red]接口错误：[/red] {exc}")
        raise SystemExit(2) from exc
    except ConfigError as exc:
        from .. import cli

        cli.console.print(f"[red]配置错误:[/red] {exc}")
        raise SystemExit(3) from exc

    return True
