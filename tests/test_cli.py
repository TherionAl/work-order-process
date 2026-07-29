from __future__ import annotations

from types import SimpleNamespace

import pytest

from work_order_process import cli
from work_order_process.api import ApiError
from work_order_process.cli import build_parser, dispatch_command
from work_order_process.cli_commands import database, diagnostics, exports, imports
from work_order_process.config import ConfigError

EXPECTED_COMMANDS = frozenset(
    {
        "run",
        "monthly-tickets",
        "template-samples",
        "mysql-init",
        "mysql-schema-status",
        "mysql-migrate",
        "mysql-drop-tables",
        "mysql-create-analysis-views",
        "mysql-import-ticket",
        "mysql-import-month",
        "mysql-import-month-v1",
        "mysql-import-year",
        "mysql-import-customers",
        "mysql-import-contacts",
        "mysql-probe-customers",
        "mysql-probe-contacts",
        "mysql-import-personnel",
        "mysql-add-partitions",
        "mysql-sync-log",
        "import-erp",
        "import-customer-account",
        "generate-revenue-summary",
        "metric-month",
        "metric-ticket",
        "probe",
        "dictionary",
    }
)


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


def _assert_command_contract(
    parser_commands: set[str],
    command_sets: tuple[frozenset[str], ...],
) -> None:
    handler_commands = set().union(*command_sets)

    assert parser_commands == EXPECTED_COMMANDS
    assert handler_commands == EXPECTED_COMMANDS
    assert sum(len(command_set) for command_set in command_sets) == len(handler_commands)


def test_command_contract_rejects_a_missing_base_command() -> None:
    with pytest.raises(AssertionError):
        _assert_command_contract(
            set(EXPECTED_COMMANDS - {"mysql-import-month-v1"}),
            (
                database.COMMANDS,
                imports.COMMANDS,
                exports.COMMANDS,
                diagnostics.COMMANDS,
            ),
        )


def test_parser_commands_match_exactly_one_handler() -> None:
    parser = build_parser()
    command_action = next(action for action in parser._actions if action.dest == "command")
    _assert_command_contract(
        set(command_action.choices),
        (
            database.COMMANDS,
            imports.COMMANDS,
            exports.COMMANDS,
            diagnostics.COMMANDS,
        ),
    )


def test_dispatch_stops_after_the_first_handler_that_accepts(monkeypatch) -> None:
    parser = build_parser()
    args = parser.parse_args(["run"])
    calls: list[str] = []

    monkeypatch.setattr(database, "handle", lambda *_: calls.append("database") or False)
    monkeypatch.setattr(imports, "handle", lambda *_: calls.append("imports") or True)
    monkeypatch.setattr(exports, "handle", lambda *_: calls.append("exports") or True)
    monkeypatch.setattr(diagnostics, "handle", lambda *_: calls.append("diagnostics") or True)

    dispatch_command(args, _settings(), parser)

    assert calls == ["database", "imports"]


def test_dispatch_calls_handlers_in_order_then_rejects_unhandled_parser_command(
    monkeypatch,
) -> None:
    parser = build_parser()
    args = parser.parse_args(["run"])
    calls: list[str] = []

    for name, module in (
        ("database", database),
        ("imports", imports),
        ("exports", exports),
        ("diagnostics", diagnostics),
    ):
        monkeypatch.setattr(module, "handle", lambda *_, name=name: calls.append(name) or False)

    with pytest.raises(RuntimeError, match="No CLI handler accepted command: run"):
        dispatch_command(args, _settings(), parser)

    assert calls == ["database", "imports", "exports", "diagnostics"]


class _DictionaryProbe(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("argv", "expects_schema_check"),
    [
        (["mysql-add-partitions"], True),
        (["mysql-import-personnel", "--personnel-file", "people.xls"], True),
        (["import-erp"], True),
        (["import-customer-account"], True),
        (["generate-revenue-summary"], True),
        (["metric-month"], False),
        (["metric-ticket"], False),
    ],
)
def test_dictionary_loads_after_schema_gate_before_command_validation(
    monkeypatch,
    argv: list[str],
    expects_schema_check: bool,
) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = SimpleNamespace(mysql=object(), dictionary_path="dictionary.pdf")
    calls: list[str] = []

    monkeypatch.setattr(cli, "assert_schema_current", lambda _: calls.append("schema"))

    def raise_dictionary_probe(_: object) -> object:
        calls.append("dictionary")
        raise _DictionaryProbe

    monkeypatch.setattr(cli.DataDictionary, "from_pdf", raise_dictionary_probe)

    with pytest.raises(_DictionaryProbe):
        dispatch_command(args, settings, parser)

    assert calls == (["schema"] if expects_schema_check else []) + ["dictionary"]


def test_personnel_validation_precedes_schema_and_dictionary(monkeypatch) -> None:
    parser = build_parser()
    args = parser.parse_args(["mysql-import-personnel"])
    calls: list[str] = []
    settings = SimpleNamespace(mysql=object(), dictionary_path="dictionary.pdf")
    monkeypatch.setattr(cli, "assert_schema_current", lambda _: calls.append("schema"))
    monkeypatch.setattr(
        cli.DataDictionary,
        "from_pdf",
        lambda _: calls.append("dictionary"),
    )

    with pytest.raises(SystemExit, match="2"):
        dispatch_command(args, settings, parser)

    assert calls == []


def test_main_does_not_translate_load_settings_config_error(monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: (_ for _ in ()).throw(ConfigError("missing")))
    monkeypatch.setattr("sys.argv", ["work_order_process", "mysql-schema-status"])

    with pytest.raises(ConfigError, match="missing"):
        cli.main()


def test_main_does_not_translate_local_handler_api_error(monkeypatch) -> None:
    settings = SimpleNamespace(mysql=object(), dictionary_path="dictionary.pdf")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "assert_schema_current", lambda _: None)
    monkeypatch.setattr(cli.DataDictionary, "from_pdf", lambda _: object())
    monkeypatch.setattr(
        imports, "import_erp_xlsx", lambda *_: (_ for _ in ()).throw(ApiError("local"))
    )
    monkeypatch.setattr("sys.argv", ["work_order_process", "import-erp", "--erp-file", "data.xlsx"])

    with pytest.raises(ApiError, match="local"):
        cli.main()


def test_main_translates_api_error_from_client_path(monkeypatch) -> None:
    settings = SimpleNamespace(mysql=object(), dictionary_path="dictionary.pdf")

    class FakeClient:
        def __init__(self, _: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def authenticate(self) -> None:
            raise ApiError("client")

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli.DataDictionary, "from_pdf", lambda _: object())
    monkeypatch.setattr(exports, "WorkOrderClient", FakeClient)
    monkeypatch.setattr("sys.argv", ["work_order_process", "run"])

    with pytest.raises(SystemExit, match="2"):
        cli.main()
