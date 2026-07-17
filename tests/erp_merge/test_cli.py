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
        [sys.executable, str(REPO_ROOT / "merge_erp_data.py"), "--help"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "ERP" in result.stdout


@pytest.mark.parametrize("write_standard", [False, True], ids=["database-only", "with-standard-output"])
def test_cli_imports_dataframe_then_exports_database_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, write_standard: bool
) -> None:
    events: list[tuple[str, object]] = []
    merged = object()
    standard: list[object] = []
    standard_output = tmp_path / "standard.xlsx"
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
        "write_standard_sheet", lambda value, path: events.append(("standard", (value, path)))
    )
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: type("Settings", (), {"mysql": "mysql-config"})(),
    )
    monkeypatch.setattr(
        cli,
        "import_erp_dataframe",
        lambda config, value: events.append(("import", (config, value)))
        or {"create_dates": ["20260717"]},
    )
    monkeypatch.setattr(
        cli,
        "export_erp_snapshot_document",
        lambda config, create_date, path: events.append(("document", (config, create_date, path))),
    )

    argv = [
        "--input-new",
        str(tmp_path / "new.xlsx"),
        "--input-old",
        str(tmp_path / "old.xlsx"),
        "--config",
        str(tmp_path / "rules.xlsx"),
        "--document-output",
        str(document_output),
    ]
    if write_standard:
        argv.extend(["--standard-output", str(standard_output)])

    cli.main(argv)

    expected = [
        ("merge", (tmp_path / "new.xlsx", tmp_path / "old.xlsx", tmp_path / "rules.xlsx")),
        ("build", (merged, ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-12-31"))),
    ]
    if write_standard:
        expected.append(("standard", (standard, standard_output)))
    expected.extend(
        [
            ("import", ("mysql-config", standard)),
            ("document", ("mysql-config", "20260717", document_output)),
        ]
    )
    assert events == expected


def test_cli_rejects_document_output_matching_standard_output_before_processing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    output = tmp_path / "nested" / ".." / "document.xlsx"

    monkeypatch.setattr(cli, "load_config", lambda: events.append("config"))
    monkeypatch.setattr(cli, "merge_erp_sources", lambda *args: events.append("merge"))
    monkeypatch.setattr(cli, "write_standard_sheet", lambda *args: events.append("standard"))
    monkeypatch.setattr(cli, "import_erp_dataframe", lambda *args: events.append("import"))
    monkeypatch.setattr(cli, "export_erp_snapshot_document", lambda *args: events.append("document"))

    with pytest.raises(ValueError, match="document-output.*standard-output.*different paths"):
        cli.main(
            [
                "--input-new",
                str(tmp_path / "new.xlsx"),
                "--input-old",
                str(tmp_path / "old.xlsx"),
                "--config",
                str(tmp_path / "rules.xlsx"),
                "--document-output",
                str(output),
                "--standard-output",
                str(tmp_path / "document.xlsx"),
            ]
        )

    assert events == []
