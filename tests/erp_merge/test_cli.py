from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from work_order_process.erp_merge import cli

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_cli_help():
    """测试子命令erp-merge可以正常调用并输出帮助信息"""
    result = subprocess.run(
        [sys.executable, "-m", "work_order_process.erp_merge.cli", "--help"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "ERP" in result.stdout


def test_standalone_script_help():
    """测试独立脚本可以正常调用并输出帮助信息"""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "merge_erp_data_20260715.py"), "--help"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "ERP" in result.stdout


@pytest.mark.parametrize("should_import", [False, True], ids=["generate-only", "with-import"])
def test_cli_import_is_explicit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, should_import: bool) -> None:
    events: list[tuple[str, object]] = []
    merged = object()
    standard: list[object] = []
    output = tmp_path / "standard.xlsx"
    periods = {
        "统计日期区间": {
            "去年起始": "2025-01-01",
            "去年截止": "2025-12-31",
            "今年起始": "2026-01-01",
            "今年截止": "2026-12-31",
        }
    }

    monkeypatch.setattr(cli, "load_config", lambda: periods)
    monkeypatch.setattr(
        cli,
        "merge_erp_sources",
        lambda new, old, rules, generated_at: events.append(("merge", (new, old, rules))) or merged,
    )
    monkeypatch.setattr(
        cli,
        "build_standard_sheet",
        lambda value, previous_period, current_period: events.append(
            ("build", (value, previous_period, current_period))
        )
        or standard,
    )
    monkeypatch.setattr(
        cli,
        "write_standard_sheet",
        lambda value, path: events.append(("write", (value, path))),
    )
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: type("Settings", (), {"mysql": "mysql-config"})(),
    )
    monkeypatch.setattr(
        cli,
        "import_erp_xlsx",
        lambda config, path: events.append(("import", (config, path))),
    )

    argv = [
        "--input-new",
        str(tmp_path / "new.xlsx"),
        "--input-old",
        str(tmp_path / "old.xlsx"),
        "--config",
        str(tmp_path / "rules.xlsx"),
        "--output",
        str(output),
    ]
    if should_import:
        argv.append("--import")

    cli.main(argv)

    assert events[:3] == [
        ("merge", (tmp_path / "new.xlsx", tmp_path / "old.xlsx", tmp_path / "rules.xlsx")),
        ("build", (merged, ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-12-31"))),
        ("write", (standard, output)),
    ]
    assert [event for event in events if event[0] == "import"] == (
        [("import", ("mysql-config", output))] if should_import else []
    )


@pytest.mark.parametrize(
    "has_document_output", [False, True], ids=["without-document", "with-document"]
)
def test_cli_document_output_is_optional_and_does_not_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, has_document_output: bool
) -> None:
    events: list[tuple[str, object]] = []
    standard: list[object] = []
    output = tmp_path / "standard.xlsx"
    document_output = tmp_path / "document.xlsx"
    periods = {
        "统计日期区间": {
            "去年起始": "2025-01-01",
            "去年截止": "2025-12-31",
            "今年起始": "2026-01-01",
            "今年截止": "2026-12-31",
        }
    }

    monkeypatch.setattr(cli, "load_config", lambda: periods)
    monkeypatch.setattr(cli, "merge_erp_sources", lambda *args: object())
    monkeypatch.setattr(cli, "build_standard_sheet", lambda *args: standard)
    monkeypatch.setattr(
        cli, "write_standard_sheet", lambda value, path: events.append(("standard", path))
    )
    monkeypatch.setattr(
        cli,
        "write_document_workbook",
        lambda value, path: events.append(("document", path)),
        raising=False,
    )
    monkeypatch.setattr(cli, "import_erp_xlsx", lambda *args: events.append(("import", args)))

    argv = [
        "--input-new",
        str(tmp_path / "new.xlsx"),
        "--input-old",
        str(tmp_path / "old.xlsx"),
        "--config",
        str(tmp_path / "rules.xlsx"),
        "--output",
        str(output),
    ]
    if has_document_output:
        argv.extend(["--document-output", str(document_output)])

    cli.main(argv)

    expected = [("standard", output)]
    if has_document_output:
        expected.append(("document", document_output))
    assert events == expected


def test_cli_rejects_document_output_matching_output_before_processing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    output = tmp_path / "nested" / ".." / "standard.xlsx"

    monkeypatch.setattr(cli, "load_config", lambda: events.append("config"))
    monkeypatch.setattr(cli, "merge_erp_sources", lambda *args: events.append("merge"))
    monkeypatch.setattr(cli, "write_standard_sheet", lambda *args: events.append("standard"))
    monkeypatch.setattr(cli, "write_document_workbook", lambda *args: events.append("document"))
    monkeypatch.setattr(cli, "import_erp_xlsx", lambda *args: events.append("import"))

    with pytest.raises(ValueError, match="document-output.*output.*different paths"):
        cli.main(
            [
                "--input-new",
                str(tmp_path / "new.xlsx"),
                "--input-old",
                str(tmp_path / "old.xlsx"),
                "--config",
                str(tmp_path / "rules.xlsx"),
                "--output",
                str(tmp_path / "standard.xlsx"),
                "--document-output",
                str(output),
                "--import",
            ]
        )

    assert events == []
