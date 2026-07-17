from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_erp_analysis_dependencies_are_not_default_runtime_dependencies() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        config = tomllib.load(file)

    default_dependencies = config["project"]["dependencies"]
    erp_dependencies = config["dependency-groups"]["erp"]

    assert not any(dependency.startswith("numpy") for dependency in default_dependencies)
    assert not any(dependency.startswith("pandas") for dependency in default_dependencies)
    assert any(dependency.startswith("numpy") for dependency in erp_dependencies)
    assert any(dependency.startswith("pandas") for dependency in erp_dependencies)
