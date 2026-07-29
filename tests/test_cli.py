from __future__ import annotations

from types import SimpleNamespace

import pytest

from work_order_process.cli import build_parser, dispatch_command


def _settings() -> SimpleNamespace:
    return SimpleNamespace()


def test_parser_defaults_match_help_text() -> None:
    parser = build_parser()
    args = parser.parse_args(["mysql-import-customers"])

    assert args.customers_source == "companies"
    assert args.contacts_source == "contacts"
    assert args.personnel_file is None
    help_text = parser.format_help()
    assert "客户导入的数据源，默认 companies" in help_text
    assert "联系人导入的数据源，默认 contacts" in help_text


def test_personnel_import_requires_explicit_file(capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    args = parser.parse_args(["mysql-import-personnel"])

    with pytest.raises(SystemExit, match="2"):
        dispatch_command(args, _settings(), parser)

    assert "--personnel-file" in capsys.readouterr().err


def test_all_existing_commands_remain_available() -> None:
    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )

    assert set(command_action.choices) >= {
        "run",
        "monthly-tickets",
        "template-samples",
        "mysql-init",
        "mysql-import-ticket",
        "mysql-import-month",
        "mysql-import-year",
        "mysql-import-customers",
        "mysql-import-contacts",
        "mysql-import-personnel",
        "mysql-add-partitions",
        "mysql-sync-log",
        "mysql-schema-status",
        "mysql-migrate",
        "import-erp",
        "import-customer-account",
        "generate-revenue-summary",
        "metric-month",
        "metric-ticket",
        "probe",
        "dictionary",
    }
