from __future__ import annotations

import pandas as pd
import pytest

from work_order_process.erp_merge.config import load_config
from work_order_process.erp_merge.mapping import (
    normalize_platform,
    add_engineer_column,
    parse_number_series,
    build_old_shared_amount,
)


@pytest.fixture
def config():
    return load_config()


def test_normalize_platform(config):
    input_series = pd.Series(["海南分公司", "浙江分公司", "未知分公司"])
    result = normalize_platform(input_series, config)
    expected = pd.Series(["广西分公司", "山东分公司", "未知分公司"])
    pd.testing.assert_series_equal(result, expected)


def test_add_engineer_column(config):
    df = pd.DataFrame({"营销平台": ["博思智合", "深圳分公司", "未知分公司"]})
    result = add_engineer_column(df, config)
    assert result.loc[0, "体系工程师"] == "黄迪"
    assert result.loc[1, "体系工程师"] == "梁通"
    assert result.loc[2, "体系工程师"] == ""


def test_parse_number_series():
    series = pd.Series(["1000", "1,000", "25%", "", "/"])
    result = parse_number_series(series)
    expected = pd.Series([1000.0, 1000.0, 0.25, 0.0, 0.0])
    pd.testing.assert_series_equal(result, expected)


def test_build_old_shared_amount(config):
    old_df = pd.DataFrame({
        "累计收入金额-去年同期": ["1000", "1,000"],
        "分成比例": ["0.25", "25%"],
    })
    result = build_old_shared_amount(old_df, "累计收入金额-去年同期", config)
    expected = pd.Series([250.0, 250.0])
    pd.testing.assert_series_equal(result, expected)
