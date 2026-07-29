"""Read-only diagnostic CLI commands."""

from __future__ import annotations

import argparse

from ..api import ApiError, WorkOrderClient
from ..config import ConfigError, Settings
from ..dictionary import DataDictionary

COMMANDS = frozenset(
    {
        "probe",
        "dictionary",
        "mysql-probe-customers",
        "mysql-probe-contacts",
    }
)


def handle(args: argparse.Namespace, settings: Settings, parser: argparse.ArgumentParser) -> bool:
    """Handle read-only API and data-dictionary diagnostics."""

    if args.command not in COMMANDS:
        return False

    from ..cli import _print_dictionary_summary, _print_entity_probe, _probe, console

    dictionary = DataDictionary.from_pdf(settings.dictionary_path)
    if args.command == "dictionary":
        output = settings.output_dir / "dictionary.json"
        dictionary.save_json(output)
        console.print(f"数据字典已保存到 {output}")
        _print_dictionary_summary(dictionary)
        return True

    try:
        with WorkOrderClient(settings) as client:
            client.authenticate()
            if args.command == "mysql-probe-customers":
                _print_entity_probe(
                    client.probe_entity_paths(
                        settings.endpoint.customer_paths,
                        "customer",
                        args.sample_size,
                    )
                )
            elif args.command == "mysql-probe-contacts":
                _print_entity_probe(
                    client.probe_entity_paths(
                        settings.endpoint.contact_paths,
                        "contact",
                        args.sample_size,
                    )
                )
            else:
                _probe(client)
    except ApiError as exc:
        from .. import cli

        cli.console.print(f"[red]接口错误：[/red] {exc}")
        raise SystemExit(2) from exc
    except ConfigError as exc:
        from .. import cli

        cli.console.print(f"[red]配置错误:[/red] {exc}")
        raise SystemExit(3) from exc

    return True
