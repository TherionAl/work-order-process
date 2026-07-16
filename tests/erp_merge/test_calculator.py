from __future__ import annotations

import pandas as pd
import pytest

from work_order_process.erp_merge.calculator import (
    calculate_period_allocation,
    add_statistical_allocation_columns,
)
from work_order_process.erp_merge.config import load_config


@pytest.fixture
def config():
    return load_config()


def test_calculate_period_allocation():
    service_start = pd.to_datetime(pd.Series(["2025-07-01", "2025-07-01"]))
    service_end = pd.to_datetime(pd.Series(["2026-06-30", "2026-06-30"]))
    product_amount = pd.Series([3650.0, 3650.0])
    contract_days = pd.Series([365, 365])
    period_start = pd.Timestamp("2025-01-01")
    period_end = pd.Timestamp("2025-12-31")

    result = calculate_period_allocation(
        service_start, service_end, product_amount, contract_days, period_start, period_end
    )
    expected = pd.Series([1840.0, 1840.0])
    pd.testing.assert_series_equal(result.round(2), expected, check_names=False)


def test_add_statistical_allocation_columns(config):
    df = pd.DataFrame({
        "合同申请年份": [2025, 2026],
        "明细运维开始开始日期": ["20250701", "20250701"],
        "明细运维结束日期": ["20260630", "20260630"],
        "产品金额": [3650.0, 3650.0],
    })

    result = add_statistical_allocation_columns(
        df, config,
        "2025-01-01", "2025-12-31", "2026-01-01", "2026-12-31"
    )

    assert "合同天数" in result.columns
    assert result.loc[0, "合同天数"] == 365
    assert result.loc[0, "去年按期分摊服务费"] == pytest.approx(1840.0, rel=1e-2)
    assert result.loc[1, "去年按期分摊服务费（去掉今年倒签的）"] == 0.0
    assert result.loc[1, "今年按期分摊服务费（加上倒签去年的服务费）"] == pytest.approx(3650.0, rel=1e-2)
