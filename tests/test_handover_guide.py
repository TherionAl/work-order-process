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
CONTRACTS = {
    "迁移命令边界（契约）": (
        (
            "`mysql-schema-status`：只读；不创建或修改表、不写入版本、不执行 DDL。",
            "`mysql-init`：只创建新库基础结构并记录已满足迁移；不执行 pending migration DDL。",
            "`mysql-migrate`：唯一显式执行 pending migration DDL 的命令。",
            "普通导入、同步和营收持久化：只写业务数据；不调用 `ensure_*_schema`，不执行隐式结构 DDL。",
        ),
        (
            "`mysql-schema-status` 会写 DDL 或创建 `schema_version`。",
            "`mysql-init` 会执行 pending migration 或调用 `migrate_schema`。",
            "`mysql-migrate` 不是唯一写入 pending migration DDL 的入口。",
            "普通导入会在写业务数据前自动建表或补列。",
        ),
    ),
    "受控重试：`work_order_process.api_transport`": (
        (
            "`429、502、503、504`",
            "`httpx.TransportError`",
            "默认最多尝试 3 次",
            "合法的数值秒数 `Retry-After`",
            "有上限的指数退避和抖动",
        ),
        (
            "`429`、`502`、`503`、`504` 不重试。",
            "任意 4xx 都重试。",
        ),
    ),
    "结构化失败与安全摘要：`work_order_process.import_failures`": (
        (
            "`stage`、`record_id`、`source_row`、`error_type`、`safe_message`",
            "默认最多保留 100 条失败详情",
            "`safe_message` 默认最多 500 个字符",
            "`failure_count` 统计总失败数，`failed_ids` 保持完整",
            "`failures_truncated=true`",
        ),
        (
            "`FailureCollector` 无限保留失败详情。",
            "`failures_truncated` 不反映详情截断。",
        ),
    ),
    "导入来源和人员文件默认值（契约）": (
        (
            "`--customers-source` 默认 `companies`",
            "`--contacts-source` 默认 `contacts`",
            "`--personnel-file` 默认 `None`，缺失时 `parser.error`",
        ),
        (
            "`--customers-source` 默认 `both`。",
            "`--contacts-source` 默认 `both`。",
            "`--personnel-file` 存在隐式默认文件。",
        ),
    ),
}
QUALITY_ONLY_COMMANDS = frozenset(
    {
        "uv run --all-groups ruff check src tests",
        "uv run --all-groups ruff format --check src tests",
        "uv run --all-groups python -m compileall -q src tests",
        "uv run work_order_process --help",
        "uv run erp-merge --help",
        "git diff --check",
    }
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
    blocks = _fenced_code_blocks(section)
    assert len(blocks) == 1, "expected one Markdown command block"
    return blocks[0]


def _assert_required_statements(section: str, expected: tuple[str, ...]) -> None:
    missing = [statement for statement in expected if statement not in section]
    assert not missing, missing


def _prohibited_claim_block(section: str) -> tuple[tuple[str, ...], tuple[int, int]]:
    match = re.search(r"禁止模式（以下均不成立）：\n((?:- .+\n)+)", section)
    assert match is not None, "missing prohibited-claim block"
    claims = tuple(re.findall(r"^- (.+)$", match.group(1), re.MULTILINE))
    return claims, match.span()


def _assert_truth_and_prohibitions(
    section: str, truth: tuple[str, ...], prohibited: tuple[str, ...]
) -> None:
    _assert_required_statements(section, truth)
    actual, span = _prohibited_claim_block(section)
    assert actual == prohibited
    outside_block = section[: span[0]] + section[span[1] :]
    contradictions = [claim for claim in prohibited if claim in outside_block]
    assert not contradictions, contradictions


def _fenced_code_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    active: list[str] | None = None
    fence = re.compile(r"^ {0,3}```")
    for line in text.splitlines():
        if active is None:
            if fence.match(line):
                active = []
            continue
        if fence.match(line):
            blocks.append([command for command in active if command])
            active = None
        else:
            active.append(line)
    assert active is None, "unterminated PowerShell or bash command block"
    return blocks


def _quality_only_commands(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if line in QUALITY_ONLY_COMMANDS
        or line.startswith("uv run --all-groups pytest --cov=work_order_process")
    ]


def _assert_no_quality_commands_outside_authority(text: str, authority: tuple[str, ...]) -> None:
    outside = [
        command
        for block in _fenced_code_blocks(text)
        if tuple(block) != authority
        for command in _quality_only_commands(block)
    ]
    assert not outside, outside


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


def test_contract_helper_rejects_truth_with_a_contradiction() -> None:
    truth, prohibited = CONTRACTS["迁移命令边界（契约）"]
    valid = "\n".join(
        (*truth, "禁止模式（以下均不成立）：", *(f"- {claim}" for claim in prohibited))
    )
    _assert_truth_and_prohibitions(valid + "\n", truth, prohibited)
    with pytest.raises(AssertionError):
        _assert_truth_and_prohibitions(valid + f"\n{prohibited[0]}\n", truth, prohibited)


def test_handover_guide_has_exact_migration_boundaries() -> None:
    section = _markdown_section(_guide_text(), "迁移命令边界（契约）", 4)
    truth, prohibited = CONTRACTS["迁移命令边界（契约）"]
    _assert_truth_and_prohibitions(section, truth, prohibited)


def test_handover_guide_has_exact_production_deployment_order() -> None:
    section = _markdown_section(_guide_text(), "9.5 版本化结构迁移的 8 步顺序", 3)
    _assert_exact_ordered_items(section, DEPLOYMENT_STEPS)


def test_handover_guide_documents_bounded_failure_contract() -> None:
    section = _markdown_section(
        _guide_text(), "结构化失败与安全摘要：`work_order_process.import_failures`", 4
    )
    truth, prohibited = CONTRACTS["结构化失败与安全摘要：`work_order_process.import_failures`"]
    _assert_truth_and_prohibitions(section, truth, prohibited)


def test_handover_guide_documents_exact_retry_contract() -> None:
    section = _markdown_section(_guide_text(), "受控重试：`work_order_process.api_transport`", 4)
    truth, prohibited = CONTRACTS["受控重试：`work_order_process.api_transport`"]
    _assert_truth_and_prohibitions(section, truth, prohibited)


def test_handover_guide_documents_import_defaults_and_personnel_requirement() -> None:
    section = _markdown_section(_guide_text(), "导入来源和人员文件默认值（契约）", 4)
    truth, prohibited = CONTRACTS["导入来源和人员文件默认值（契约）"]
    _assert_truth_and_prohibitions(section, truth, prohibited)


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
            "函数体内延迟导入 `ticket_import` 的同名实现",
            "`_fetch_month_ticket_rows`、`_prefetch_ticket_entities`、`_str_or_none`、"
            "`_filter_ticket_rows_for_import`、`_same_datetime`、`_commit_batch_atomic`、"
            "`_fetch_batch_details`、`_commit_batch`、`_merge_failure_collectors`、"
            "`_merge_failure_payload`、`_safe_rollback`",
            "`_write_sync_log` 是 module-level import 后的 direct delegate",
            "`SYNC_TASK_LOG_DDL` 是 module-level re-export",
            "`cli.main` / `cli.build_parser` 保留在 `cli.py`",
            "`cli.dispatch_command` 在函数内加载 `database`、`imports`、`exports`、`diagnostics` handlers",
            "module-level import seams 保留给 monkeypatch",
            "旧调用方无需改动",
        ),
    )


def test_handover_guide_has_one_ordered_authoritative_quality_gate() -> None:
    text = _guide_text()
    section = _markdown_section(_guide_text(), "本地完整质量门槛（唯一权威）", 4)
    assert _code_block_lines(section) == list(QUALITY_GATE_COMMANDS)
    _assert_no_quality_commands_outside_authority(text, QUALITY_GATE_COMMANDS)


@pytest.mark.parametrize(
    "opening_fence", ("```powershell", "```bash", "```sh", "```shell", "```", "```python")
)
def test_quality_gate_helper_rejects_an_external_partial_block(opening_fence: str) -> None:
    with pytest.raises(AssertionError):
        _assert_no_quality_commands_outside_authority(
            f"{opening_fence}\nuv run work_order_process --help\n```\n",
            QUALITY_GATE_COMMANDS,
        )


@pytest.mark.parametrize("opening_indent", range(4))
@pytest.mark.parametrize("closing_indent", range(4))
def test_quality_gate_helper_scans_commonmark_indented_fences(
    opening_indent: int, closing_indent: int
) -> None:
    command = "uv run work_order_process --help"
    text = f"{' ' * opening_indent}```sh\n{command}\n{' ' * closing_indent}```\n"

    assert _fenced_code_blocks(text) == [[command]]
    with pytest.raises(AssertionError, match=re.escape(command)):
        _assert_no_quality_commands_outside_authority(text, QUALITY_GATE_COMMANDS)


def test_four_space_fences_do_not_change_fenced_block_state() -> None:
    command = "uv run work_order_process --help"
    text = f"    ```sh\nignored outside block\n    ```\n```sh\n    ```\n{command}\n   ```\n"

    assert _fenced_code_blocks(text) == [["    ```", command]]


def test_production_template_syncs_runtime_dependencies_before_restart() -> None:
    guide_section = _markdown_section(_guide_text(), "9.4 部署模板", 3)
    commands = _code_block_lines(guide_section)
    assert commands[:5] == [
        "cd /opt/work_order_process",
        "git status --short",
        "git pull --ff-only",
        "uv sync --locked --no-dev",
        "sudo systemctl restart work-order-daily.service",
    ]
    assert "`git status --short` 的输出必须为空" in guide_section
    assert "有输出先人工处理" in guide_section
    assert "不得以本地第 8.2 节检查替代服务器状态" in guide_section
    assert commands.index("uv sync --locked --no-dev") < commands.index(
        "sudo systemctl restart work-order-daily.service"
    )
    production_text = (PROJECT_ROOT / "docs" / "production_operations.md").read_text(
        encoding="utf-8"
    )
    assert "uv sync --locked --no-dev" in production_text
    assert "/opt/work_order_process/.venv/bin/python" in production_text


def test_customer_account_terminology_is_consistent_in_scope() -> None:
    for relative_path in ("README.md", "docs/database_usage.md"):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert not re.findall(r"(?<!客户)台账", text), relative_path
        assert "客户客户台账" not in text
