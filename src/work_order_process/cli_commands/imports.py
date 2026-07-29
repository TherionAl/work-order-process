"""MySQL import CLI commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..api import ApiError, WorkOrderClient
from ..config import ConfigError, Settings
from ..customer_account_import import import_customer_account_xlsx
from ..dictionary import DataDictionary
from ..erp_import import import_erp_xlsx
from ..mysql_storage import (
    import_contacts_to_mysql,
    import_customers_to_mysql,
    import_month_tickets_serial,
    import_month_tickets_to_mysql,
    import_ticket_detail_to_mysql,
    import_year_tickets_to_mysql,
)
from ..personnel_import import import_personnel_xls_to_mysql

COMMANDS = frozenset(
    {
        "mysql-import-ticket",
        "mysql-import-month",
        "mysql-import-month-v1",
        "mysql-import-year",
        "mysql-import-customers",
        "mysql-import-contacts",
        "mysql-import-personnel",
        "import-erp",
        "import-customer-account",
    }
)


def handle(args: argparse.Namespace, settings: Settings, parser: argparse.ArgumentParser) -> bool:
    """Handle commands that write imported data to MySQL."""

    if args.command not in COMMANDS:
        return False

    from ..cli import (
        _print_customer_account_import_report,
        _print_customer_contact_report,
        _print_erp_import_report,
        _print_mysql_import_report,
        _print_mysql_month_report,
        _print_mysql_year_report,
        _print_personnel_import_report,
        _resolve_sources,
        assert_schema_current,
    )

    if args.command == "mysql-import-personnel" and not args.personnel_file:
        parser.error("mysql-import-personnel requires --personnel-file")

    assert_schema_current(settings.mysql)

    dictionary = DataDictionary.from_pdf(settings.dictionary_path)

    if args.command == "mysql-import-personnel":
        report = import_personnel_xls_to_mysql(settings.mysql, Path(args.personnel_file))
        _print_personnel_import_report(report)
        return True

    if args.command == "import-erp":
        if not args.erp_file:
            raise ApiError("import-erp 需要传入 --erp-file。")
        report = import_erp_xlsx(settings.mysql, Path(args.erp_file))
        _print_erp_import_report(report)
        return True

    if args.command == "import-customer-account":
        if not args.customer_account_file:
            raise ApiError("import-customer-account 需要传入 --customer-account-file。")
        if not args.create_date:
            raise ApiError("import-customer-account 需要传入 --create-date。")
        report = import_customer_account_xlsx(
            settings.mysql,
            Path(args.customer_account_file),
            args.create_date,
            args.sheet,
        )
        _print_customer_account_import_report(report)
        return True

    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()

            if args.command == "mysql-import-ticket":
                if not args.ticket_id:
                    raise ApiError("Please pass --ticket-id for mysql-import-ticket.")
                report = import_ticket_detail_to_mysql(
                    settings.mysql, dictionary, client, args.ticket_id
                )
                _print_mysql_import_report(report)
            elif args.command == "mysql-import-month":
                if args.month is None:
                    raise ApiError("mysql-import-month 需要传入 --month。")
                report = import_month_tickets_to_mysql(
                    settings.mysql,
                    dictionary,
                    client,
                    year=args.year,
                    month=args.month,
                    per_page=args.per_page,
                    limit_per_month=args.limit_per_month,
                    max_workers=args.max_workers,
                    batch_size=args.batch_size,
                    api_rate_limit=args.api_rate_limit,
                )
                _print_mysql_month_report(report)
            elif args.command == "mysql-import-month-v1":
                if args.month is None:
                    raise ApiError("mysql-import-month-v1 需要传入 --month。")
                report = import_month_tickets_serial(
                    settings.mysql,
                    dictionary,
                    client,
                    year=args.year,
                    month=args.month,
                    per_page=args.per_page,
                    limit_per_month=args.limit_per_month,
                    output_dir=settings.output_dir,
                )
                _print_mysql_month_report(report)
            elif args.command == "mysql-import-year":
                report = import_year_tickets_to_mysql(
                    settings.mysql,
                    dictionary,
                    client,
                    year=args.year,
                    months=[args.month] if args.month is not None else None,
                    per_page=args.per_page,
                    limit_per_month=args.limit_per_month,
                    max_workers=args.max_workers,
                    batch_size=args.batch_size,
                    api_rate_limit=args.api_rate_limit,
                    output_dir=settings.output_dir,
                )
                _print_mysql_year_report(report)
            elif args.command == "mysql-import-customers":
                sources = _resolve_sources(args.customers_source, ["companies", "customers"])
                report = import_customers_to_mysql(
                    settings.mysql,
                    client,
                    sources=sources,
                    require_nonempty=not args.allow_empty,
                    max_records=args.max_records,
                )
                _print_customer_contact_report("customers", report)
            else:
                sources = _resolve_sources(args.contacts_source, ["contacts", "company_contacts"])
                report = import_contacts_to_mysql(
                    settings.mysql,
                    client,
                    sources=sources,
                    require_nonempty=not args.allow_empty,
                    max_records=args.max_records,
                )
                _print_customer_contact_report("contacts", report)
    except ApiError as exc:
        from .. import cli

        cli.console.print(f"[red]接口错误：[/red] {exc}")
        raise SystemExit(2) from exc
    except ConfigError as exc:
        from .. import cli

        cli.console.print(f"[red]配置错误:[/red] {exc}")
        raise SystemExit(3) from exc

    return True
