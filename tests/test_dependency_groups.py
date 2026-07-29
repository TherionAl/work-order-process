from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUALITY_TOOL_NAMES = {"coverage", "pytest-cov", "ruff"}


def _pyproject() -> dict[str, Any]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def _requirement_names(requirements: list[str]) -> set[str]:
    return {canonicalize_name(Requirement(item).name) for item in requirements}


def _assert_quality_tools_development_only(config: dict[str, Any]) -> None:
    dev_names = _requirement_names(config["dependency-groups"]["dev"])
    runtime_names = _requirement_names(config["project"]["dependencies"])

    missing_from_dev = QUALITY_TOOL_NAMES - dev_names
    runtime_overlap = QUALITY_TOOL_NAMES & runtime_names
    assert not missing_from_dev, f"quality tools missing from dev dependencies: {missing_from_dev}"
    assert not runtime_overlap, f"quality tools present in runtime dependencies: {runtime_overlap}"


def _project_gitignore_ignores(root: Path, candidate: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"core.excludesFile={os.devnull}",
            "check-ignore",
            "-v",
            "--no-index",
            "--",
            candidate,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr
    if result.returncode == 1:
        return False

    metadata, separator, ignored_path = result.stdout.rstrip("\r\n").partition("\t")
    assert separator and ignored_path == candidate, result.stdout
    source, line_number, pattern = metadata.rsplit(":", maxsplit=2)
    assert line_number.isdigit(), result.stdout
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = root / source_path
    return (
        bool(pattern)
        and not pattern.startswith("!")
        and source_path.resolve() == (root / ".gitignore").resolve()
    )


def test_erp_analysis_dependencies_are_not_default_runtime_dependencies() -> None:
    config = _pyproject()

    default_dependencies = config["project"]["dependencies"]
    erp_dependencies = config["dependency-groups"]["erp"]

    assert not any(dependency.startswith("numpy") for dependency in default_dependencies)
    assert not any(dependency.startswith("pandas") for dependency in default_dependencies)
    assert any(dependency.startswith("numpy") for dependency in erp_dependencies)
    assert any(dependency.startswith("pandas") for dependency in erp_dependencies)


def test_quality_tools_are_development_only() -> None:
    _assert_quality_tools_development_only(_pyproject())


def test_yaml_parser_is_development_only() -> None:
    config = _pyproject()
    dev_names = _requirement_names(config["dependency-groups"]["dev"])
    runtime_names = _requirement_names(config["project"]["dependencies"])

    assert "pyyaml" in dev_names
    assert "pyyaml" not in runtime_names


def test_coverage_runtime_file_is_ignored() -> None:
    for candidate in (".coverage", "htmlcov/"):
        assert _project_gitignore_ignores(PROJECT_ROOT, candidate)


@pytest.mark.parametrize(
    "dev_requirements",
    [
        ["ruffle>=1", "pytest-cov>=1", "coverage>=1"],
        ["ruff>=1", "pytest-cov-helper>=1", "coverage>=1"],
        ["ruff>=1", "pytest-cov>=1", "coverage-helper>=1"],
    ],
)
def test_quality_contract_rejects_prefix_collisions(dev_requirements: list[str]) -> None:
    config = {
        "dependency-groups": {"dev": dev_requirements},
        "project": {"dependencies": []},
    }

    with pytest.raises(AssertionError):
        _assert_quality_tools_development_only(config)


@pytest.mark.parametrize("runtime_tool", ["ruff>=1", "pytest-cov>=1", "coverage>=1"])
def test_quality_contract_rejects_runtime_quality_tools(runtime_tool: str) -> None:
    config = {
        "dependency-groups": {"dev": ["ruff>=1", "pytest-cov>=1", "coverage>=1"]},
        "project": {"dependencies": [runtime_tool]},
    }

    with pytest.raises(AssertionError):
        _assert_quality_tools_development_only(config)


@pytest.mark.parametrize(
    ("gitignore_text", "candidate"),
    [
        (".coverage\n!.coverage\n", ".coverage"),
        (" .coverage\n", ".coverage"),
        ("htmlcov/\n!htmlcov/\n", "htmlcov/.sentinel"),
    ],
)
def test_git_ignore_contract_rejects_negation_and_leading_whitespace(
    tmp_path: Path,
    gitignore_text: str,
    candidate: str,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(gitignore_text, encoding="utf-8")

    assert not _project_gitignore_ignores(tmp_path, candidate)


@pytest.mark.parametrize(
    ("gitignore_text", "expected"),
    [
        ("htmlcov/index.html\nhtmlcov/assets/style.css\n\n", False),
        ("htmlcov/\n", True),
    ],
)
def test_htmlcov_ignore_contract_requires_directory_rule(
    tmp_path: Path,
    gitignore_text: str,
    expected: bool,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(gitignore_text, encoding="utf-8")

    assert _project_gitignore_ignores(tmp_path, "htmlcov/") is expected


@pytest.mark.parametrize("exclude_source", ["info", "global"])
def test_project_ignore_contract_rejects_non_project_sources(
    tmp_path: Path,
    exclude_source: str,
) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    if exclude_source == "info":
        exclude_file = tmp_path / ".git" / "info" / "exclude"
    else:
        exclude_file = tmp_path / "global-ignore"
        subprocess.run(
            ["git", "config", "core.excludesFile", str(exclude_file)],
            cwd=tmp_path,
            check=True,
        )
    exclude_file.write_text("htmlcov/\n", encoding="utf-8")

    assert not _project_gitignore_ignores(tmp_path, "htmlcov/.sentinel")
