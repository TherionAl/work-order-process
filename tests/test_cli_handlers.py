from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from work_order_process import cli
from work_order_process.api import ApiError
from work_order_process.cli import build_parser
from work_order_process.cli_commands import database, diagnostics, exports, imports
from work_order_process.config import ConfigError


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        mysql=SimpleNamespace(host="db", port=3306, database="work_order"),
        dictionary_path=tmp_path / "dictionary.pdf",
        output_dir=tmp_path / "output",
        endpoint=SimpleNamespace(customer_paths=["/companies"], contact_paths=["/users"]),
    )


def _args(*argv: str):
    parser = build_parser()
    return parser, parser.parse_args(list(argv))


@pytest.mark.parametrize(
    "handler", [database.handle, imports.handle, exports.handle, diagnostics.handle]
)
def test_handlers_return_false_for_nonmatching_command(handler, tmp_path: Path) -> None:
    parser, args = _args("run")
    args.command = "not-owned"

    assert handler(args, _settings(tmp_path), parser) is False


def test_schema_status_is_read_only(monkeypatch, tmp_path: Path) -> None:
    parser, args = _args("mysql-schema-status")
    status = SimpleNamespace(
        current_version="v1",
        target_version="v2",
        pending_versions=("v2",),
        drifted_versions=(),
    )
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        cli, "schema_status", lambda mysql: calls.append(("status", mysql)) or status
    )
    monkeypatch.setattr(
        cli,
        "migrate_schema",
        lambda _: pytest.fail("schema-status must not migrate"),
    )
    monkeypatch.setattr(
        cli,
        "assert_schema_current",
        lambda _: pytest.fail("schema-status must remain a read-only inspection"),
    )

    assert database.handle(args, _settings(tmp_path), parser) is True
    assert calls == [("status", _settings(tmp_path).mysql)]


def test_mysql_migrate_is_the_only_lifecycle_command_that_invokes_migration(
    monkeypatch, tmp_path: Path
) -> None:
    parser, args = _args("mysql-migrate")
    before = SimpleNamespace(
        current_version="v1",
        target_version="v2",
        pending_versions=("v2",),
        drifted_versions=(),
    )
    after = SimpleNamespace(
        current_version="v2",
        target_version="v2",
        pending_versions=(),
        drifted_versions=(),
    )
    calls: list[str] = []
    monkeypatch.setattr(cli, "schema_status", lambda _: calls.append("status") or before)
    monkeypatch.setattr(cli, "migrate_schema", lambda _: calls.append("migrate") or after)

    assert database.handle(args, _settings(tmp_path), parser) is True
    assert calls == ["status", "migrate"]


@pytest.mark.parametrize(
    ("argv", "operation_name"),
    [
        (("mysql-create-analysis-views",), "views"),
        (("mysql-drop-tables",), "drop"),
        (("mysql-add-partitions", "--months-ahead", "2"), "partitions"),
    ],
)
def test_database_mutations_run_schema_preflight_before_selected_operation(
    monkeypatch, tmp_path: Path, argv: tuple[str, ...], operation_name: str
) -> None:
    parser, args = _args(*argv)
    calls: list[str] = []
    monkeypatch.setattr(cli, "assert_schema_current", lambda _: calls.append("preflight"))
    monkeypatch.setattr(cli.DataDictionary, "from_pdf", lambda _: calls.append("dictionary"))
    monkeypatch.setattr(
        cli,
        "create_customer_contact_analysis_views",
        lambda _: calls.append("views"),
    )
    monkeypatch.setattr(cli, "drop_mysql_tables", lambda _: calls.append("drop"))
    monkeypatch.setattr(cli, "generate_months_ahead", lambda months: [f"M{months}"])
    monkeypatch.setattr(
        cli,
        "add_future_partitions",
        lambda _, months: calls.append("partitions") or months,
    )

    assert database.handle(args, _settings(tmp_path), parser) is True
    assert calls == ["preflight", "dictionary", operation_name]


def test_personnel_import_missing_file_fails_through_parser_error_before_writes(
    monkeypatch, tmp_path: Path
) -> None:
    parser, args = _args("mysql-import-personnel")
    monkeypatch.setattr(
        cli,
        "assert_schema_current",
        lambda _: pytest.fail("validation must precede schema/database work"),
    )
    monkeypatch.setattr(
        imports,
        "import_personnel_xls_to_mysql",
        lambda *_: pytest.fail("missing file must not invoke import"),
    )

    with pytest.raises(SystemExit, match="2"):
        imports.handle(args, _settings(tmp_path), parser)


def test_personnel_import_preflights_then_invokes_only_personnel_domain_operation(
    monkeypatch, tmp_path: Path
) -> None:
    parser, args = _args("mysql-import-personnel", "--personnel-file", str(tmp_path / "people.xls"))
    calls: list[object] = []
    report = {"table": "personnel", "total_count": 2, "affected_rows": 2}
    monkeypatch.setattr(cli, "assert_schema_current", lambda _: calls.append("preflight"))
    monkeypatch.setattr(imports.DataDictionary, "from_pdf", lambda _: calls.append("dictionary"))
    monkeypatch.setattr(
        imports,
        "import_personnel_xls_to_mysql",
        lambda mysql, path: calls.append(("personnel", mysql, path)) or report,
    )
    monkeypatch.setattr(cli, "_print_personnel_import_report", lambda value: calls.append(value))

    settings = _settings(tmp_path)
    assert imports.handle(args, settings, parser) is True
    assert calls == [
        "preflight",
        "dictionary",
        ("personnel", settings.mysql, tmp_path / "people.xls"),
        report,
    ]


@pytest.mark.parametrize(
    ("argv", "required_argument"),
    [
        (("import-erp",), "--erp-file"),
        (("import-customer-account",), "--customer-account-file"),
        (
            ("import-customer-account", "--customer-account-file", "account.xlsx"),
            "--create-date",
        ),
    ],
)
def test_import_file_and_date_validation_uses_parser_error_before_domain_import(
    monkeypatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, ...],
    required_argument: str,
) -> None:
    parser, args = _args(*argv)
    calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "assert_schema_current",
        lambda _: calls.append("preflight"),
    )
    monkeypatch.setattr(
        imports.DataDictionary,
        "from_pdf",
        lambda _: calls.append("dictionary") or object(),
    )
    monkeypatch.setattr(
        imports,
        "import_erp_xlsx",
        lambda *_: pytest.fail("invalid arguments must not invoke ERP import"),
    )
    monkeypatch.setattr(
        imports,
        "import_customer_account_xlsx",
        lambda *_: pytest.fail("invalid arguments must not invoke customer-account import"),
    )

    with pytest.raises(SystemExit, match="2"):
        imports.handle(args, _settings(tmp_path), parser)

    assert calls == ["preflight", "dictionary"]
    assert required_argument in capsys.readouterr().err


def test_customer_account_import_passes_file_date_and_sheet_after_preflight(
    monkeypatch, tmp_path: Path
) -> None:
    parser, args = _args(
        "import-customer-account",
        "--customer-account-file",
        "account.xlsx",
        "--create-date",
        "2026-07-01",
        "--sheet",
        "客户台账",
    )
    settings = _settings(tmp_path)
    calls: list[object] = []
    report = {"source_file": "account.xlsx", "affected_rows": 1}
    monkeypatch.setattr(cli, "assert_schema_current", lambda _: calls.append("preflight"))
    monkeypatch.setattr(imports.DataDictionary, "from_pdf", lambda _: calls.append("dictionary"))
    monkeypatch.setattr(
        imports,
        "import_customer_account_xlsx",
        lambda mysql, path, date, sheet: (
            calls.append(("account", mysql, path, date, sheet)) or report
        ),
    )
    monkeypatch.setattr(
        cli, "_print_customer_account_import_report", lambda value: calls.append(value)
    )

    assert imports.handle(args, settings, parser) is True
    assert calls == [
        "preflight",
        "dictionary",
        ("account", settings.mysql, Path("account.xlsx"), "2026-07-01", "客户台账"),
        report,
    ]


def test_erp_import_passes_selected_workbook_after_preflight(monkeypatch, tmp_path: Path) -> None:
    parser, args = _args("import-erp", "--erp-file", "erp.xlsx")
    settings = _settings(tmp_path)
    calls: list[object] = []
    report = {"source_file": "erp.xlsx", "affected_rows": 3}
    monkeypatch.setattr(cli, "assert_schema_current", lambda _: calls.append("preflight"))
    monkeypatch.setattr(imports.DataDictionary, "from_pdf", lambda _: calls.append("dictionary"))
    monkeypatch.setattr(
        imports,
        "import_erp_xlsx",
        lambda mysql, path: calls.append(("erp", mysql, path)) or report,
    )
    monkeypatch.setattr(cli, "_print_erp_import_report", lambda value: calls.append(value))

    assert imports.handle(args, settings, parser) is True
    assert calls == [
        "preflight",
        "dictionary",
        ("erp", settings.mysql, Path("erp.xlsx")),
        report,
    ]


@pytest.mark.parametrize(
    ("argv", "expected_operation", "expected_argument"),
    [
        (("mysql-import-ticket", "--ticket-id", "T1"), "ticket", "T1"),
        (("mysql-import-month", "--month", "7"), "month", 7),
        (("mysql-import-month-v1", "--month", "8"), "month-v1", 8),
        (("mysql-import-year", "--month", "9"), "year", [9]),
        (("mysql-import-customers", "--max-records", "4"), "customers", 4),
        (("mysql-import-contacts", "--max-records", "5"), "contacts", 5),
    ],
)
def test_api_import_commands_invoke_only_selected_domain_operation(
    monkeypatch,
    tmp_path: Path,
    argv: tuple[str, ...],
    expected_operation: str,
    expected_argument: object,
) -> None:
    parser, args = _args(*argv)
    settings = _settings(tmp_path)
    calls: list[object] = []
    report = {"operation": expected_operation}

    class FakeClient:
        def __init__(self, value) -> None:
            assert value is settings

        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *_args) -> None:
            calls.append("exit")

        def authenticate(self) -> None:
            calls.append("authenticate")

    monkeypatch.setattr(cli, "assert_schema_current", lambda _: calls.append("preflight"))
    monkeypatch.setattr(imports.DataDictionary, "from_pdf", lambda _: "dictionary")
    monkeypatch.setattr(imports, "WorkOrderClient", FakeClient)
    monkeypatch.setattr(
        imports,
        "import_ticket_detail_to_mysql",
        lambda mysql, dictionary, client, ticket_id: calls.append(("ticket", ticket_id)) or report,
    )
    monkeypatch.setattr(
        imports,
        "import_month_tickets_to_mysql",
        lambda mysql, dictionary, client, **kwargs: (
            calls.append(("month", kwargs["month"])) or report
        ),
    )
    monkeypatch.setattr(
        imports,
        "import_month_tickets_serial",
        lambda mysql, dictionary, client, **kwargs: (
            calls.append(("month-v1", kwargs["month"])) or report
        ),
    )
    monkeypatch.setattr(
        imports,
        "import_year_tickets_to_mysql",
        lambda mysql, dictionary, client, **kwargs: (
            calls.append(("year", kwargs["months"])) or report
        ),
    )
    monkeypatch.setattr(cli, "_resolve_sources", lambda source, both: (source,))
    monkeypatch.setattr(
        imports,
        "import_customers_to_mysql",
        lambda mysql, client, **kwargs: (
            calls.append(("customers", kwargs["max_records"])) or report
        ),
    )
    monkeypatch.setattr(
        imports,
        "import_contacts_to_mysql",
        lambda mysql, client, **kwargs: calls.append(("contacts", kwargs["max_records"])) or report,
    )
    monkeypatch.setattr(cli, "_print_mysql_import_report", lambda value: calls.append(value))
    monkeypatch.setattr(cli, "_print_mysql_month_report", lambda value: calls.append(value))
    monkeypatch.setattr(cli, "_print_mysql_year_report", lambda value: calls.append(value))
    monkeypatch.setattr(
        cli,
        "_print_customer_contact_report",
        lambda table, value: calls.append((table, value)),
    )

    assert imports.handle(args, settings, parser) is True
    assert calls[:3] == ["preflight", "enter", "authenticate"]
    assert calls[3] == (expected_operation, expected_argument)
    if expected_operation in {"customers", "contacts"}:
        assert calls[4] == (expected_operation, report)
    else:
        assert calls[4] == report
    assert calls[5] == "exit"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (("metric-month", "--year", "2026", "--month", "7"), ("month", 2026, 7)),
        (("metric-ticket", "--ticket-id", "T-9"), ("ticket", "T-9")),
    ],
)
def test_metric_export_commands_invoke_the_selected_domain_operation(
    monkeypatch,
    tmp_path: Path,
    argv: tuple[str, ...],
    expected: tuple[object, ...],
) -> None:
    parser, args = _args(*argv)
    settings = _settings(tmp_path)
    calls: list[object] = []
    report = {"result_count": 1}
    monkeypatch.setattr(exports.DataDictionary, "from_pdf", lambda _: object())
    monkeypatch.setattr(
        exports,
        "export_month_time_metrics",
        lambda mysql, **kwargs: calls.append(("month", kwargs["year"], kwargs["month"])) or report,
    )
    monkeypatch.setattr(
        exports,
        "export_ticket_time_metrics",
        lambda mysql, **kwargs: calls.append(("ticket", kwargs["ticket_id"])) or report,
    )
    monkeypatch.setattr(cli, "_print_time_metric_report", lambda value: calls.append(value))

    assert exports.handle(args, settings, parser) is True
    assert calls == [expected, report]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (("metric-month",), "metric-month requires --month"),
        (("metric-ticket",), "metric-ticket requires --ticket-id"),
        (("generate-revenue-summary",), "需要传入 --month"),
        (
            ("generate-revenue-summary", "--month", "7"),
            "需要传入 --revenue-target-file",
        ),
    ],
)
def test_export_required_arguments_fail_before_domain_operation(
    monkeypatch,
    tmp_path: Path,
    argv: tuple[str, ...],
    message: str,
) -> None:
    parser, args = _args(*argv)
    monkeypatch.setattr(cli, "assert_schema_current", lambda _: None)
    monkeypatch.setattr(exports.DataDictionary, "from_pdf", lambda _: object())

    with pytest.raises(ApiError, match=message):
        exports.handle(args, _settings(tmp_path), parser)


@pytest.mark.parametrize(
    ("argv", "expected_operation", "expected_months"),
    [
        (("run", "--year", "2026", "--month", "7"), "year", [7]),
        (("monthly-tickets", "--year", "2026"), "monthly", None),
        (
            ("template-samples", "--year", "2026", "--month", "8"),
            "template",
            8,
        ),
    ],
)
def test_api_export_commands_pass_selected_month_scope_to_domain_operation(
    monkeypatch,
    tmp_path: Path,
    argv: tuple[str, ...],
    expected_operation: str,
    expected_months: object,
) -> None:
    parser, args = _args(*argv)
    settings = _settings(tmp_path)
    calls: list[object] = []
    report = {"operation": expected_operation}

    class FakeClient:
        def __init__(self, value) -> None:
            assert value is settings

        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *_args) -> None:
            calls.append("exit")

        def authenticate(self) -> None:
            calls.append("authenticate")

    monkeypatch.setattr(exports.DataDictionary, "from_pdf", lambda _: "dictionary")
    monkeypatch.setattr(exports, "WorkOrderClient", FakeClient)
    monkeypatch.setattr(
        exports,
        "export_year_monthly_tickets_and_samples",
        lambda output, dictionary, client, **kwargs: (
            calls.append(("year", kwargs["months"])) or report
        ),
    )
    monkeypatch.setattr(
        exports,
        "export_year_monthly_tickets",
        lambda output, client, **kwargs: calls.append(("monthly", kwargs["months"])) or report,
    )
    monkeypatch.setattr(
        exports,
        "export_month_template_samples",
        lambda output, dictionary, client, **kwargs: (
            calls.append(("template", kwargs["month"])) or report
        ),
    )
    monkeypatch.setattr(cli, "_print_year_report", lambda value: calls.append(value))
    monkeypatch.setattr(cli, "_print_monthly_ticket_report", lambda value: calls.append(value))
    monkeypatch.setattr(cli, "_print_template_sample_report", lambda value: calls.append(value))

    assert exports.handle(args, settings, parser) is True
    assert calls == [
        "enter",
        "authenticate",
        (expected_operation, expected_months),
        report,
        "exit",
    ]


class _Dictionary:
    def __init__(self) -> None:
        self.saved_to: Path | None = None

    def save_json(self, path: Path) -> None:
        self.saved_to = path


def test_dictionary_diagnostic_writes_dictionary_without_opening_api(
    monkeypatch, tmp_path: Path
) -> None:
    parser, args = _args("dictionary")
    dictionary = _Dictionary()
    summaries: list[object] = []
    monkeypatch.setattr(diagnostics.DataDictionary, "from_pdf", lambda _: dictionary)
    monkeypatch.setattr(cli, "_print_dictionary_summary", summaries.append)
    monkeypatch.setattr(
        diagnostics,
        "WorkOrderClient",
        lambda _: pytest.fail("dictionary command must not open an API client"),
    )

    assert diagnostics.handle(args, _settings(tmp_path), parser) is True
    assert dictionary.saved_to == tmp_path / "output" / "dictionary.json"
    assert summaries == [dictionary]


def test_customer_probe_uses_configured_paths_entity_and_sample_size(
    monkeypatch, tmp_path: Path
) -> None:
    parser, args = _args("mysql-probe-customers", "--sample-size", "4")
    reports = [{"path": "/companies", "status": "ok", "count": 1}]
    calls: list[object] = []

    class FakeClient:
        def __init__(self, settings) -> None:
            calls.append(("init", settings))

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            calls.append("close")

        def authenticate(self) -> None:
            calls.append("authenticate")

        def probe_entity_paths(self, paths, entity_type, sample_size):
            calls.append(("probe", paths, entity_type, sample_size))
            return reports

    settings = _settings(tmp_path)
    monkeypatch.setattr(diagnostics.DataDictionary, "from_pdf", lambda _: object())
    monkeypatch.setattr(diagnostics, "WorkOrderClient", FakeClient)
    monkeypatch.setattr(cli, "_print_entity_probe", lambda value: calls.append(value))

    assert diagnostics.handle(args, settings, parser) is True
    assert calls == [
        ("init", settings),
        "authenticate",
        ("probe", ["/companies"], "customer", 4),
        reports,
        "close",
    ]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("mysql-probe-contacts", ("probe", ["/users"], "contact", 3)),
        ("probe", "general-probe"),
    ],
)
def test_other_diagnostic_commands_select_contact_or_general_probe(
    monkeypatch, tmp_path: Path, command: str, expected: object
) -> None:
    parser, args = _args(command)
    settings = _settings(tmp_path)
    calls: list[object] = []

    class FakeClient:
        def __init__(self, value) -> None:
            assert value is settings

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def authenticate(self) -> None:
            calls.append("authenticate")

        def probe_entity_paths(self, paths, entity_type, sample_size):
            calls.append(("probe", paths, entity_type, sample_size))
            return [{"status": "ok"}]

    monkeypatch.setattr(diagnostics.DataDictionary, "from_pdf", lambda _: object())
    monkeypatch.setattr(diagnostics, "WorkOrderClient", FakeClient)
    monkeypatch.setattr(cli, "_print_entity_probe", lambda value: calls.append(value))
    monkeypatch.setattr(cli, "_probe", lambda client: calls.append("general-probe"))

    assert diagnostics.handle(args, settings, parser) is True
    assert calls[0] == "authenticate"
    assert expected in calls


@pytest.mark.parametrize(
    ("error", "exit_code", "message"),
    [
        (ApiError("api unavailable"), "2", "api unavailable"),
        (ConfigError("bad configuration"), "3", "bad configuration"),
    ],
)
def test_diagnostic_boundary_translates_project_errors_to_cli_exit_codes(
    monkeypatch,
    tmp_path: Path,
    error: Exception,
    exit_code: str,
    message: str,
) -> None:
    parser, args = _args("probe")
    printed: list[str] = []

    class FailingClient:
        def __init__(self, settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def authenticate(self) -> None:
            raise error

    monkeypatch.setattr(diagnostics.DataDictionary, "from_pdf", lambda _: object())
    monkeypatch.setattr(diagnostics, "WorkOrderClient", FailingClient)
    monkeypatch.setattr(
        cli.console,
        "print",
        lambda *values: printed.append(" ".join(str(value) for value in values)),
    )

    with pytest.raises(SystemExit, match=exit_code) as caught:
        diagnostics.handle(args, _settings(tmp_path), parser)

    assert str(caught.value) == exit_code
    assert len(printed) == 1
    assert message in printed[0]
    assert "traceback" not in printed[0].lower()
