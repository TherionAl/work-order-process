from __future__ import annotations

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
