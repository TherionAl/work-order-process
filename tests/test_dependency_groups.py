from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict[str, object]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def test_erp_analysis_dependencies_are_not_default_runtime_dependencies() -> None:
    config = _pyproject()

    default_dependencies = config["project"]["dependencies"]
    erp_dependencies = config["dependency-groups"]["erp"]

    assert not any(dependency.startswith("numpy") for dependency in default_dependencies)
    assert not any(dependency.startswith("pandas") for dependency in default_dependencies)
    assert any(dependency.startswith("numpy") for dependency in erp_dependencies)
    assert any(dependency.startswith("pandas") for dependency in erp_dependencies)


def test_quality_tools_are_development_only() -> None:
    config = _pyproject()
    dev = config["dependency-groups"]["dev"]
    runtime = config["project"]["dependencies"]

    assert any(item.startswith("ruff") for item in dev)
    assert any(item.startswith("pytest-cov") for item in dev)
    assert any(item.startswith("coverage") for item in dev)
    assert not any(item.startswith("ruff") for item in runtime)


def test_coverage_runtime_file_is_ignored() -> None:
    text = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".coverage" in text
