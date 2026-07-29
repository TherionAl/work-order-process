from __future__ import annotations

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


def _gitignore_patterns(text: str) -> set[str]:
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _assert_coverage_artifacts_ignored(text: str) -> None:
    patterns = _gitignore_patterns(text)
    assert ".coverage" in patterns
    assert "htmlcov/" in patterns


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


def test_coverage_runtime_file_is_ignored() -> None:
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    _assert_coverage_artifacts_ignored(text)


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
    "gitignore_text",
    [
        "# .coverage\nhtmlcov/\n",
        ".coverage.disabled\nhtmlcov/\n",
        ".coverage\nhtmlcov.disabled/\n",
    ],
)
def test_coverage_ignore_contract_rejects_non_exact_patterns(gitignore_text: str) -> None:
    with pytest.raises(AssertionError):
        _assert_coverage_artifacts_ignored(gitignore_text)
