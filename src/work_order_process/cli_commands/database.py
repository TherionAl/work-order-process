"""Database lifecycle and maintenance CLI commands."""

from __future__ import annotations

import argparse

from ..config import Settings


COMMANDS = frozenset(
    {
        "mysql-init",
        "mysql-schema-status",
        "mysql-migrate",
        "mysql-create-analysis-views",
        "mysql-drop-tables",
        "mysql-add-partitions",
        "mysql-sync-log",
    }
)

_SCHEMA_MUTATING_COMMANDS = frozenset(
    {
        "mysql-create-analysis-views",
        "mysql-drop-tables",
        "mysql-add-partitions",
    }
)


def handle(args: argparse.Namespace, settings: Settings, parser: argparse.ArgumentParser) -> bool:
    """Handle database lifecycle and maintenance commands."""

    if args.command not in COMMANDS:
        return False

    from .. import cli

    if args.command == "mysql-schema-status":
        status = cli.schema_status(settings.mysql)
        cli.console.print(
            f"current={status.current_version}, target={status.target_version}, "
            f"pending={list(status.pending_versions)}, "
            f"drifted={list(status.drifted_versions)}"
        )
        return True

    if args.command == "mysql-migrate":
        before = cli.schema_status(settings.mysql)
        status = cli.migrate_schema(settings.mysql)
        applied = [
            version
            for version in before.pending_versions
            if version not in status.pending_versions
        ]
        cli.console.print(
            f"applied={applied}, remaining={list(status.pending_versions)}, "
            f"current={status.current_version}, target={status.target_version}"
        )
        return True

    if args.command == "mysql-init":
        cli.ensure_mysql_schema(settings.mysql)
        migration_status = cli.record_satisfied_schema(settings.mysql)
        partitions = cli.get_existing_partitions(settings.mysql)
        month_count = len(partitions) - (1 if "pmax" in partitions else 0)
        cli.console.print(
            f"[green]MySQL 数据库初始化完成[/green]\n"
            f"地址: {settings.mysql.host}:{settings.mysql.port}/{settings.mysql.database}\n"
            f"已创建 5 张表，{month_count} 个月分区 + pmax\n"
            f"migration current={migration_status.current_version}, "
            f"pending={list(migration_status.pending_versions)}"
        )
        return True

    if args.command in _SCHEMA_MUTATING_COMMANDS:
        cli.assert_schema_current(settings.mysql)

    if args.command == "mysql-create-analysis-views":
        cli.create_customer_contact_analysis_views(settings.mysql)
        cli.console.print("[green]客户/联系人分析视图已创建。[/green]")
    elif args.command == "mysql-drop-tables":
        cli.drop_mysql_tables(settings.mysql)
        cli.console.print("[yellow]全部 5 张表已删除。[/yellow]")
    elif args.command == "mysql-add-partitions":
        months_list = cli.generate_months_ahead(args.months_ahead)
        created = cli.add_future_partitions(settings.mysql, months_list)
        if created:
            cli.console.print(f"[green]新建分区: {', '.join(created)}[/green]")
        else:
            cli.console.print("[dim]所有月份分区均已存在，无需新建。[/dim]")
    else:
        with cli.WorkOrderClient(settings) as client:
            client.authenticate()
            cli._print_sync_log(settings)

    return True
