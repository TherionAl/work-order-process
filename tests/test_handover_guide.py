from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUIDE_PATH = PROJECT_ROOT / "docs" / "project_handover_guide.md"
SOURCE_ROOT = PROJECT_ROOT / "src" / "work_order_process"


def _guide_text() -> str:
    return GUIDE_PATH.read_text(encoding="utf-8")


def _cli_commands() -> set[str]:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from work_order_process.cli import main; main()",
            "--help",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    match = re.search(r"\{([^}]*\brun\b[^}]*)\}", result.stdout)
    assert match is not None
    return {command.strip() for command in match.group(1).split(",") if command.strip()}


def _source_symbols() -> set[str]:
    symbols: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT).with_suffix("")
        module = ".".join(relative.parts)
        if module == "__init__":
            continue
        symbols.add(f"work_order_process.{module}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(
                node,
                (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                symbols.add(f"work_order_process.{module}.{node.name}")
    return symbols


def test_handover_guide_has_required_sections() -> None:
    text = _guide_text()
    for heading in (
        "快速接手",
        "架构与数据流",
        "配置与依赖",
        "命令参考",
        "模块与函数参考",
        "数据库与业务流程",
        "开发与测试",
        "生产运行",
        "故障排查",
        "维护检查表",
    ):
        assert heading in text


def test_handover_guide_covers_every_cli_command() -> None:
    text = _guide_text()
    missing = sorted(command for command in _cli_commands() if f"`{command}`" not in text)
    assert not missing, missing


def test_handover_guide_indexes_all_source_symbols() -> None:
    text = _guide_text()
    missing = sorted(symbol for symbol in _source_symbols() if f"`{symbol}`" not in text)
    assert not missing, missing


def test_readme_links_handover_guide() -> None:
    text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "[项目接手手册](docs/project_handover_guide.md)" in text


def test_handover_guide_local_links_exist() -> None:
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", _guide_text())
    local_links = [
        link.split("#", maxsplit=1)[0]
        for link in links
        if not link.startswith(("http://", "https://", "#"))
    ]
    assert local_links
    missing = sorted(link for link in local_links if not (GUIDE_PATH.parent / link).exists())
    assert not missing, missing


DEPLOYMENT_STEPS = (
    "部署一个已经通过验证的提交。",
    "停止 `daily_runner`，确认没有并发调度器。",
    "运行 `mysql-schema-status`。",
    "审核待办版本、checksum 状态和迁移前备份状态。",
    "明确运行 `mysql-migrate`。",
    "再次运行 `mysql-schema-status`，必须显示 current 状态。",
    "启动 `daily_runner`。",
    "检查 `sync_task_log` 和服务日志。",
)
QUALITY_GATE_COMMANDS = (
    "uv sync --all-groups --locked",
    "uv lock --check",
    "uv run --all-groups ruff check src tests",
    "uv run --all-groups ruff format --check src tests",
    "uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 "
    "--cov-config=pyproject.toml -q",
    "uv run --all-groups python -m compileall -q src tests",
    "uv run work_order_process --help",
    "uv run erp-merge --help",
    "git diff --check",
    "git status --short",
)


def _markdown_section(text: str, heading: str, level: int) -> str:
    marker = "#" * level
    match = re.search(rf"^{re.escape(marker)} {re.escape(heading)}$", text, re.MULTILINE)
    assert match is not None, f"missing heading: {heading}"
    next_heading = re.search(rf"^#{{1,{level}}} ", text[match.end() :], re.MULTILINE)
    end = match.end() if next_heading is None else match.end() + next_heading.start()
    return text[match.end() : end]


def _ordered_items(section: str) -> list[str]:
    return re.findall(r"^\d+\. (.+)$", section, re.MULTILINE)


def _assert_exact_ordered_items(section: str, expected: tuple[str, ...]) -> None:
    assert _ordered_items(section) == list(expected)


def _code_block_lines(section: str) -> list[str]:
    match = re.search(r"```(?:powershell)?\n(.*?)\n```", section, re.DOTALL)
    assert match is not None, "missing command block"
    return [line for line in match.group(1).splitlines() if line]


def _assert_required_statements(section: str, expected: tuple[str, ...]) -> None:
    missing = [statement for statement in expected if statement not in section]
    assert not missing, missing


def test_contract_helpers_reject_order_negation_and_omission() -> None:
    _assert_exact_ordered_items("1. first\n2. second", ("first", "second"))
    with pytest.raises(AssertionError):
        _assert_exact_ordered_items("1. second\n2. first", ("first", "second"))
    with pytest.raises(AssertionError):
        _assert_required_statements(
            "`mysql-migrate` 不执行 pending migration DDL。",
            ("`mysql-migrate`：唯一显式执行 pending migration DDL 的命令。",),
        )
    with pytest.raises(AssertionError):
        _assert_required_statements("stage, record_id", ("stage", "record_id", "safe_message"))


def test_handover_guide_has_exact_migration_boundaries() -> None:
    section = _markdown_section(_guide_text(), "迁移命令边界（契约）", 4)
    _assert_required_statements(
        section,
        (
            "`mysql-schema-status`：只读；不创建或修改表、不写入版本、不执行 DDL。",
            "`mysql-init`：只创建新库基础结构并记录已满足迁移；不执行 pending migration DDL。",
            "`mysql-migrate`：唯一显式执行 pending migration DDL 的命令。",
        ),
    )


def test_handover_guide_has_exact_production_deployment_order() -> None:
    section = _markdown_section(_guide_text(), "9.5 版本化结构迁移的 8 步顺序", 3)
    _assert_exact_ordered_items(section, DEPLOYMENT_STEPS)


def test_handover_guide_documents_bounded_failure_contract() -> None:
    section = _markdown_section(
        _guide_text(), "结构化失败与安全摘要：`work_order_process.import_failures`", 4
    )
    _assert_required_statements(
        section,
        (
            "`stage`、`record_id`、`source_row`、`error_type`、`safe_message`",
            "默认最多保留 100 条失败详情",
            "`safe_message` 默认最多 500 个字符",
            "`failure_count` 统计总失败数，`failed_ids` 保持完整",
            "`failures_truncated=true`",
        ),
    )


def test_handover_guide_documents_exact_retry_contract() -> None:
    section = _markdown_section(_guide_text(), "受控重试：`work_order_process.api_transport`", 4)
    _assert_required_statements(
        section,
        (
            "`429、502、503、504`",
            "`httpx.TransportError`",
            "默认最多尝试 3 次",
            "合法的数值秒数 `Retry-After`",
            "有上限的指数退避和抖动",
        ),
    )


def test_handover_guide_documents_import_defaults_and_personnel_requirement() -> None:
    section = _markdown_section(_guide_text(), "导入来源和人员文件默认值（契约）", 4)
    _assert_required_statements(
        section,
        (
            "`--customers-source` 默认 `companies`",
            "`--contacts-source` 默认 `contacts`",
            "`--personnel-file` 默认 `None`，缺失时 `parser.error`",
        ),
    )


def test_personnel_usage_requires_an_explicit_file_for_every_command() -> None:
    text = (PROJECT_ROOT / "docs" / "personnel_mysql_usage.md").read_text(encoding="utf-8")
    commands = re.findall(r"uv run work_order_process mysql-import-personnel[^\n]*", text)
    assert commands
    assert all("--personnel-file" in command for command in commands)
    assert re.search(r"默认\s*没有人员文件", text)
    assert 'parser.error("mysql-import-personnel requires --personnel-file")' in text


def test_handover_guide_indexes_compatibility_facades() -> None:
    section = _markdown_section(_guide_text(), "兼容层/实现映射", 4)
    _assert_required_statements(
        section,
        (
            "`mysql_storage.import_month_tickets_to_mysql` / `import_month_tickets_serial` / "
            "`import_year_tickets_to_mysql`",
            "`ticket_import` 的同名实现",
            "`_fetch_month_ticket_rows`、`_prefetch_ticket_entities`、`_str_or_none`、"
            "`_filter_ticket_rows_for_import`、`_same_datetime`、`_commit_batch_atomic`、"
            "`_fetch_batch_details`、`_commit_batch`、`_merge_failure_collectors`、"
            "`_merge_failure_payload`、`_safe_rollback`",
            "`_write_sync_log` / `SYNC_TASK_LOG_DDL`",
            "`sync_log.write_sync_log` / `sync_log.SYNC_TASK_LOG_DDL`",
            "`cli.main` / `cli.build_parser` / `cli.dispatch_command`",
            "`cli_commands` handlers",
            "monkeypatch seams",
            "函数内延迟导入",
            "旧调用方无需改动",
        ),
    )


def test_handover_guide_has_one_ordered_authoritative_quality_gate() -> None:
    text = _guide_text()
    section = _markdown_section(_guide_text(), "本地完整质量门槛（唯一权威）", 4)
    assert _code_block_lines(section) == list(QUALITY_GATE_COMMANDS)
    assert text.count("uv sync --all-groups --locked") == 1


def test_customer_account_terminology_is_consistent_in_scope() -> None:
    for relative_path in ("README.md", "docs/database_usage.md"):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert not re.findall(r"(?<!客户)台账", text), relative_path
        assert "客户客户台账" not in text
