from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_cli_help():
    """测试子命令erp-merge可以正常调用并输出帮助信息"""
    result = subprocess.run(
        [sys.executable, "-m", "work_order_process.erp_merge.cli", "--help"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "ERP" in result.stdout


def test_standalone_script_help():
    """测试独立脚本可以正常调用并输出帮助信息"""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "merge_erp_data_20260715.py"), "--help"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "ERP" in result.stdout
