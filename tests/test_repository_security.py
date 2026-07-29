from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {"", ".md", ".py", ".toml", ".yml", ".yaml"}
CI_QUALITY_COMMANDS = (
    "uv run --all-groups ruff format --check src tests",
    "uv run --all-groups ruff check src tests",
    "uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 -q",
)
COVERAGE_DENOMINATOR_FILTERS = {
    "exclude_also",
    "exclude_lines",
    "include",
    "omit",
    "partial_branches",
}


def _pyproject() -> dict[str, Any]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def _yaml_plain_scalar(text: str) -> str:
    in_single_quote = False
    in_double_quote = False
    escaped = False
    for index, char in enumerate(text):
        if char == "\\" and in_double_quote and not escaped:
            escaped = True
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote and not escaped:
            in_double_quote = not in_double_quote
        elif char == "#" and not in_single_quote and not in_double_quote:
            if index == 0 or text[index - 1].isspace():
                text = text[:index]
                break
        escaped = False

    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return json.loads(value)
    return value


def _workflow_step_run_commands(workflow: str) -> list[str]:
    commands: list[str] = []
    steps_indent: int | None = None
    item_indent: int | None = None
    in_step = False

    for line in workflow.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)

        if steps_indent is None:
            if re.fullmatch(r"steps:\s*(?:#.*)?", stripped):
                steps_indent = indent
            continue
        if indent <= steps_indent:
            steps_indent = None
            item_indent = None
            in_step = False
            continue

        step_item = re.fullmatch(r"-\s*(.*)", stripped)
        if item_indent is None and step_item:
            item_indent = indent
        if item_indent is None:
            continue
        if indent == item_indent and step_item:
            in_step = True
            inline_run = re.fullmatch(r"run:\s*(.+)", step_item.group(1))
            if inline_run:
                commands.append(_yaml_plain_scalar(inline_run.group(1)))
            continue
        if not in_step or indent <= item_indent:
            continue

        run_scalar = re.fullmatch(r"run:\s*(.+)", stripped)
        if run_scalar:
            commands.append(_yaml_plain_scalar(run_scalar.group(1)))

    return commands


def _assert_ci_quality_commands(commands: list[str]) -> None:
    cursor = 0
    for expected in CI_QUALITY_COMMANDS:
        try:
            cursor = commands.index(expected, cursor) + 1
        except ValueError as exc:
            raise AssertionError(f"missing or out-of-order CI command: {expected}") from exc


def _assert_coverage_configuration(config: dict[str, Any]) -> None:
    coverage = config["tool"]["coverage"]
    run = coverage["run"]
    report = coverage["report"]

    assert run.get("branch") is True
    assert run.get("source") == ["work_order_process"]
    assert report.get("fail_under") == 70
    for section_name, section in (("run", run), ("report", report)):
        narrowing = COVERAGE_DENOMINATOR_FILTERS & section.keys()
        assert not narrowing, f"coverage.{section_name} narrows the denominator: {narrowing}"


def test_tracked_text_files_do_not_contain_known_credentials() -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
    ).splitlines()
    content = "\n".join(
        (PROJECT_ROOT / relative_path).read_text(encoding="utf-8", errors="ignore")
        for relative_path in tracked
        if (PROJECT_ROOT / relative_path).suffix.lower() in TEXT_SUFFIXES
    )
    forbidden_username = "bosssoft" + "2021"
    forbidden_password = "Bosi_" + "soft2024"

    assert forbidden_username not in content
    assert forbidden_password not in content


def test_ci_runs_locked_python_314_test_suite() -> None:
    text = (PROJECT_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert 'python-version: "3.14"' in text
    assert "uv lock --check" in text
    assert "uv sync --all-groups --locked" in text
    assert "uv run --all-groups pytest -q" in text


def test_ci_runs_lint_format_and_coverage_gate() -> None:
    text = (PROJECT_ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")

    _assert_ci_quality_commands(_workflow_step_run_commands(text))
    _assert_coverage_configuration(_pyproject())


def test_ci_run_extractor_ignores_comments() -> None:
    workflow = """jobs:
  pytest:
    steps:
      # - run: uv run --all-groups ruff check src tests
      - name: Actual step
        run: uv run --all-groups pytest -q
"""

    assert _workflow_step_run_commands(workflow) == ["uv run --all-groups pytest -q"]


@pytest.mark.parametrize(
    "commands",
    [
        [CI_QUALITY_COMMANDS[1], CI_QUALITY_COMMANDS[0], CI_QUALITY_COMMANDS[2]],
        [
            CI_QUALITY_COMMANDS[0],
            f"{CI_QUALITY_COMMANDS[1]} --unsafe-fixes",
            CI_QUALITY_COMMANDS[2],
        ],
    ],
)
def test_ci_contract_rejects_wrong_order_and_non_exact_commands(commands: list[str]) -> None:
    with pytest.raises(AssertionError):
        _assert_ci_quality_commands(commands)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("run", "omit", ["work_order_process/cli.py"]),
        ("report", "include", ["work_order_process/api.py"]),
        ("report", "exclude_also", ["if TYPE_CHECKING:"]),
    ],
)
def test_coverage_contract_rejects_denominator_narrowing(
    section: str,
    key: str,
    value: object,
) -> None:
    config = {
        "tool": {
            "coverage": {
                "run": {"branch": True, "source": ["work_order_process"]},
                "report": {"fail_under": 70},
            }
        }
    }
    config["tool"]["coverage"][section][key] = value

    with pytest.raises(AssertionError):
        _assert_coverage_configuration(config)


def test_readme_local_document_links_exist() -> None:
    text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    local_links = [
        link.split("#", maxsplit=1)[0]
        for link in links
        if not link.startswith(("http://", "https://", "#"))
    ]
    assert local_links
    assert all((PROJECT_ROOT / link).exists() for link in local_links)


def test_runtime_artifacts_are_ignored() -> None:
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "logs/" in text
    assert "backups/" in text


def test_environment_example_uses_non_root_mysql_account() -> None:
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "WORKORDER_MYSQL_USER=workorder" in text
    assert "WORKORDER_MYSQL_USER=root" not in text


def test_referenced_markdown_documents_exist() -> None:
    markdown_files = [
        PROJECT_ROOT / relative_path
        for relative_path in subprocess.check_output(
            ["git", "-c", "core.quotepath=false", "ls-files", "*.md"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
        ).splitlines()
    ]
    references = set()
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        references.update(re.findall(r"docs/[^\s`)]+\.md", text))

    assert references
    assert all((PROJECT_ROOT / reference).exists() for reference in references)
