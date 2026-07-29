from __future__ import annotations

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


def _git_path_is_ignored(root: Path, candidate: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", candidate],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr
    return result.returncode == 0


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
    assert _git_path_is_ignored(PROJECT_ROOT, ".coverage")
    assert _git_path_is_ignored(PROJECT_ROOT, "htmlcov/.sentinel")


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

    assert not _git_path_is_ignored(tmp_path, candidate)
