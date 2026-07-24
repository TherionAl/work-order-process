from __future__ import annotations

import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {"", ".md", ".py", ".toml", ".yml", ".yaml"}


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
    text = (PROJECT_ROOT / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    assert 'python-version: "3.14"' in text
    assert "uv lock --check" in text
    assert "uv sync --all-groups --locked" in text
    assert "uv run --all-groups pytest -q" in text


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
