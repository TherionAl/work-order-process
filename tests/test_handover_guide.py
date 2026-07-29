from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path


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
    return {
        command.strip()
        for command in match.group(1).split(",")
        if command.strip()
    }


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
    missing = sorted(
        command for command in _cli_commands() if f"`{command}`" not in text
    )
    assert not missing, missing


def test_handover_guide_indexes_all_source_symbols() -> None:
    text = _guide_text()
    missing = sorted(
        symbol for symbol in _source_symbols() if f"`{symbol}`" not in text
    )
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
    missing = sorted(
        link for link in local_links if not (GUIDE_PATH.parent / link).exists()
    )
    assert not missing, missing
