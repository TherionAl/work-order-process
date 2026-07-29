from __future__ import annotations

import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from coverage import Coverage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {"", ".md", ".py", ".toml", ".yml", ".yaml"}
CI_QUALITY_COMMANDS = (
    "uv run --all-groups ruff format --check src tests",
    "uv run --all-groups ruff check src tests",
    "uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 "
    "--cov-config=pyproject.toml -q",
)
COVERAGE_DENOMINATOR_FILTERS = {
    "exclude_also",
    "exclude_lines",
    "include",
    "omit",
    "partial_also",
    "partial_branches",
    "partial_branches_always",
}
COVERAGE_DEFAULT_FILTER_OPTIONS = (
    "report:exclude_lines",
    "report:partial_branches",
    "report:partial_branches_always",
)


def _pyproject() -> dict[str, Any]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def _workflow_jobs(workflow: str) -> dict[str, object]:
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict), "workflow must be a YAML mapping"
    jobs = parsed.get("jobs")
    assert isinstance(jobs, dict), "workflow must define a jobs mapping"
    return jobs


def _is_explicit_false(value: object) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def _pytest_addopts_tokens(addopts: object) -> list[str]:
    values = [addopts] if isinstance(addopts, str) else addopts
    assert isinstance(values, list), "pytest addopts must be a string or list"

    tokens: list[str] = []
    for value in values:
        assert isinstance(value, str), "pytest addopts entries must be strings"
        tokens.extend(shlex.split(value, posix=True))
    return tokens


def _assert_addopts_tokens_safe(addopts: object) -> None:
    tokens = _pytest_addopts_tokens(addopts)
    for index, token in enumerate(tokens):
        assert token != "--no-cov", "pytest addopts must not disable coverage"
        assert not (token == "--cov" or token.startswith(("--cov=", "--cov-"))), (
            f"pytest addopts must not override coverage: {token}"
        )
        assert token not in {"-pno:cov", "-p=no:cov"}, (
            "pytest addopts must not disable the coverage plugin"
        )
        if token == "-p" and index + 1 < len(tokens):
            assert tokens[index + 1] != "no:cov", (
                "pytest addopts must not disable the coverage plugin"
            )


def _assert_pytest_addopts_safe(config: dict[str, Any]) -> None:
    addopts = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts")
    if addopts is not None:
        _assert_addopts_tokens_safe(addopts)


def _assert_environment_safe(environment: object) -> None:
    if environment is None:
        return
    assert isinstance(environment, dict), "environment must be a mapping"
    normalized = {str(key).upper(): value for key, value in environment.items()}
    forbidden = {"COVERAGE_PROCESS_START", "COVERAGE_RCFILE"} & normalized.keys()
    assert not forbidden, f"environment overrides coverage configuration: {forbidden}"
    if "PYTEST_ADDOPTS" in normalized:
        _assert_addopts_tokens_safe(normalized["PYTEST_ADDOPTS"])


def _environment_is_safe(environment: object) -> bool:
    try:
        _assert_environment_safe(environment)
    except AssertionError:
        return False
    return True


def _job_enforces_quality_commands(job: object) -> bool:
    if not isinstance(job, dict):
        return False
    if "if" in job or "needs" in job:
        return False
    if "continue-on-error" in job and not _is_explicit_false(job["continue-on-error"]):
        return False
    if not _environment_is_safe(job.get("env")):
        return False
    steps = job.get("steps")
    if not isinstance(steps, list):
        return False
    if any(isinstance(step, dict) and not _environment_is_safe(step.get("env")) for step in steps):
        return False

    cursor = 0
    for expected in CI_QUALITY_COMMANDS:
        for index in range(cursor, len(steps)):
            step = steps[index]
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str) or run.strip() != expected:
                continue
            if "if" in step:
                continue
            if "continue-on-error" in step and not _is_explicit_false(step["continue-on-error"]):
                continue
            cursor = index + 1
            break
        else:
            return False
    return True


def _assert_ci_quality_workflow(workflow: str) -> None:
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict), "workflow must be a YAML mapping"
    _assert_environment_safe(parsed.get("env"))
    jobs = _workflow_jobs(workflow)
    assert any(_job_enforces_quality_commands(job) for job in jobs.values()), (
        "one CI job must enforce exact format, lint, and coverage commands in order"
    )


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


def _load_effective_coverage_configuration(
    config_file: Path | str = PROJECT_ROOT / "pyproject.toml",
) -> Coverage:
    coverage = Coverage(config_file=str(config_file))
    coverage.load()
    return coverage


def _assert_effective_coverage_configuration(coverage: Coverage) -> None:
    defaults = Coverage(config_file=False)
    defaults.load()

    assert coverage.get_option("run:branch") is True
    assert coverage.get_option("run:source") == ["work_order_process"]
    assert coverage.get_option("report:fail_under") == 70
    for option in (
        "run:omit",
        "run:include",
        "report:omit",
        "report:include",
        "report:exclude_also",
        "report:partial_also",
    ):
        assert not coverage.get_option(option), f"effective coverage option must be empty: {option}"
    for option in COVERAGE_DEFAULT_FILTER_OPTIONS:
        assert coverage.get_option(option) == defaults.get_option(option), (
            f"effective coverage option must retain only built-in defaults: {option}"
        )


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


def test_ci_runs_lint_format_and_coverage_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (PROJECT_ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")

    _assert_ci_quality_workflow(text)
    _assert_pytest_addopts_safe(_pyproject())
    monkeypatch.chdir(PROJECT_ROOT)
    _assert_effective_coverage_configuration(_load_effective_coverage_configuration())


def test_ci_run_extractor_ignores_comments() -> None:
    workflow = """jobs:
  pytest:
    steps:
      # - run: uv run --all-groups ruff format --check src tests
      # - run: uv run --all-groups ruff check src tests
      # - run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
      - name: Actual step
        run: uv run --all-groups pytest -q
"""

    with pytest.raises(AssertionError):
        _assert_ci_quality_workflow(workflow)


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
    steps = "".join(f"      - run: {command}\n" for command in commands)
    workflow = f"jobs:\n  quality:\n    steps:\n{steps}"

    with pytest.raises(AssertionError):
        _assert_ci_quality_workflow(workflow)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("run", "omit", ["work_order_process/cli.py"]),
        ("report", "include", ["work_order_process/api.py"]),
        ("report", "exclude_also", ["if TYPE_CHECKING:"]),
        ("report", "partial_also", ["if debug:"]),
        ("report", "partial_branches_always", ["if debug:"]),
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


@pytest.mark.parametrize(
    "workflow",
    [
        """jobs:
  quality:
    steps:
      - name: Nested keys are not steps
        env:
          run: uv run --all-groups ruff format --check src tests
        with:
          run: uv run --all-groups ruff check src tests
        run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
""",
        """jobs:
  format:
    steps:
      - run: uv run --all-groups ruff format --check src tests
  tests:
    steps:
      - run: uv run --all-groups ruff check src tests
      - run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
""",
        """jobs:
  quality:
    steps:
      - run: uv run --all-groups ruff format --check src tests
        if: false
      - run: uv run --all-groups ruff check src tests
      - run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
""",
        """jobs:
  quality:
    steps:
      - run: uv run --all-groups ruff format --check src tests
      - run: uv run --all-groups ruff check src tests
        continue-on-error: true
      - run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
""",
        """jobs:
  quality:
    if: false
    steps:
      - run: uv run --all-groups ruff format --check src tests
      - run: uv run --all-groups ruff check src tests
      - run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
""",
        """jobs:
  quality:
    steps:
      - name: Block content cannot create step keys
        env:
          SCRIPT: |
            run: uv run --all-groups ruff format --check src tests
            run: uv run --all-groups ruff check src tests
        run: uv run --all-groups pytest --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
""",
    ],
)
def test_ci_contract_rejects_nested_cross_job_and_bypass_mutations(workflow: str) -> None:
    with pytest.raises(AssertionError):
        _assert_ci_quality_workflow(workflow)


def test_ci_contract_accepts_literal_and_folded_run_scalars() -> None:
    workflow = """jobs:
  quality:
    steps:
      - run: |
          uv run --all-groups ruff format --check src tests
      - run: >-
          uv run --all-groups ruff check
          src tests
      - run: >-
          uv run --all-groups pytest
          --cov=work_order_process --cov-fail-under=70 --cov-config=pyproject.toml -q
"""

    _assert_ci_quality_workflow(workflow)


@pytest.mark.parametrize("job_guard", ["needs: build", "continue-on-error: true"])
def test_ci_contract_rejects_job_dependencies_and_error_bypass(job_guard: str) -> None:
    workflow = f"""jobs:
  quality:
    {job_guard}
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
"""

    with pytest.raises(AssertionError):
        _assert_ci_quality_workflow(workflow)


def test_ci_contract_allows_explicit_false_error_guards() -> None:
    workflow = f"""jobs:
  quality:
    continue-on-error: false
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
        continue-on-error: false
      - run: {CI_QUALITY_COMMANDS[1]}
        continue-on-error: false
      - run: {CI_QUALITY_COMMANDS[2]}
        continue-on-error: false
"""

    _assert_ci_quality_workflow(workflow)


@pytest.mark.parametrize(
    "workflow",
    [
        f"""env:
  COVERAGE_RCFILE: decoy.rc
jobs:
  quality:
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
""",
        f"""jobs:
  quality:
    env:
      COVERAGE_PROCESS_START: decoy.rc
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
""",
        f"""jobs:
  quality:
    env:
      PYTEST_ADDOPTS: --cov-config=decoy.rc
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
""",
        f"""jobs:
  quality:
    env:
      PYTEST_ADDOPTS: --cov-fail-under=0
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
""",
        f"""jobs:
  quality:
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
        env:
          PYTEST_ADDOPTS: --no-cov
""",
    ],
)
def test_ci_contract_rejects_coverage_environment_overrides(workflow: str) -> None:
    with pytest.raises(AssertionError):
        _assert_ci_quality_workflow(workflow)


@pytest.mark.parametrize(
    "addopts",
    [
        "--no-cov",
        ["-q", "--cov-config=decoy.rc"],
        "--cov-fail-under 0",
        ["-p", "no:cov"],
    ],
)
def test_pytest_addopts_contract_rejects_coverage_overrides(addopts: object) -> None:
    config = {"tool": {"pytest": {"ini_options": {"addopts": addopts}}}}

    with pytest.raises(AssertionError):
        _assert_pytest_addopts_safe(config)


def test_pytest_addopts_contract_allows_unrelated_options() -> None:
    config = {"tool": {"pytest": {"ini_options": {"addopts": ["-q", "--strict-markers"]}}}}

    _assert_pytest_addopts_safe(config)


def test_yaml_loader_preserves_truthy_job_ids() -> None:
    jobs = _workflow_jobs("jobs:\n  on: {}\n  true: {}\n")

    assert set(jobs) == {"on", "true"}


def test_ci_contract_accepts_truthy_job_id_without_key_collision() -> None:
    workflow = f"""jobs:
  on:
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
"""

    _assert_ci_quality_workflow(workflow)


def test_ci_contract_rejects_quality_commands_split_across_truthy_job_ids() -> None:
    workflow = f"""jobs:
  on:
    steps:
      - run: {CI_QUALITY_COMMANDS[0]}
  true:
    steps:
      - run: {CI_QUALITY_COMMANDS[1]}
      - run: {CI_QUALITY_COMMANDS[2]}
"""

    with pytest.raises(AssertionError):
        _assert_ci_quality_workflow(workflow)


def test_explicit_coverage_config_ignores_higher_priority_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """[tool.coverage.run]
branch = true
source = ["work_order_process"]

[tool.coverage.report]
fail_under = 70
""",
        encoding="utf-8",
    )
    (tmp_path / ".coveragerc").write_text(
        """[run]
branch = false
source = decoy
omit = */cli.py

[report]
fail_under = 1
partial_also = if debug:
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("COVERAGE_RCFILE", str(tmp_path / ".coveragerc"))
    monkeypatch.chdir(tmp_path)

    coverage = _load_effective_coverage_configuration(tmp_path / "pyproject.toml")
    _assert_effective_coverage_configuration(coverage)


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
