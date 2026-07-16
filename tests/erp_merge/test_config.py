from __future__ import annotations

from pathlib import Path

import pytest

from work_order_process.erp_merge.config import load_config

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_load_default_config():
    config = load_config()
    assert "统计日期区间" in config
    assert config["统计日期区间"]["去年起始"] == "2025-01-01"
    assert "营销平台映射" in config
    assert config["营销平台映射"]["海南分公司"] == "广西分公司"


def test_load_custom_config():
    config = load_config(FIXTURE_DIR / "test_rules.toml")
    assert config["统计日期区间"]["去年起始"] == "2024-01-01"


def test_config_missing_required_key():
    with pytest.raises(ValueError, match="配置缺少必填项"):
        load_config(FIXTURE_DIR / "invalid_rules.toml")
