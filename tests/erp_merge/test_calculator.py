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


@pytest.mark.parametrize(
    ("service_start", "service_end", "period_start", "period_end", "expected_days"),
    [
        ("2025-01-01", "2025-01-10", "2025-02-01", "2025-02-28", 0),
        ("2025-01-01", "2025-01-10", "2025-01-01", "2025-01-10", 10),
        ("2025-01-01", "2025-12-31", "2025-03-01", "2025-03-31", 31),
        ("2025-01-01", "2025-01-10", "2024-12-20", "2025-01-05", 5),
        ("2025-01-05", "2025-01-20", "2025-01-15", "2025-02-01", 6),
    ],
)
def test_calculate_period_allocation_boundaries(
    service_start, service_end, period_start, period_end, expected_days
):
    result = calculate_period_allocation(
        pd.to_datetime(pd.Series([service_start])),
        pd.to_datetime(pd.Series([service_end])),
        pd.Series([100.0]),
        pd.Series([10]),
        pd.Timestamp(period_start),
        pd.Timestamp(period_end),
    )

    assert result.iloc[0] == pytest.approx(expected_days * 10)


def test_calculate_period_allocation_uses_inclusive_overlap_days():
    result = calculate_period_allocation(
        pd.to_datetime(pd.Series(["2026-07-10"])),
        pd.to_datetime(pd.Series(["2027-07-09"])),
        pd.Series([25000.0]),
        pd.Series([365]),
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-12-31"),
    )

    assert result.iloc[0] == pytest.approx(25000 * 175 / 365)


def test_calculate_period_allocation_returns_zero_for_invalid_inputs():
    result = calculate_period_allocation(
        pd.to_datetime(pd.Series([None, "2025-01-10"])),
        pd.to_datetime(pd.Series([None, "2025-01-01"])),
        pd.Series([100.0, 100.0]),
        pd.Series([10, 0]),
        pd.Timestamp("2025-01-01"),
        pd.Timestamp("2025-12-31"),
    )

    pd.testing.assert_series_equal(result, pd.Series([0.0, 0.0]), check_names=False)


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


def test_add_statistical_allocation_preserves_inclusive_contract_days_for_invalid_period(
    config,
):
    df = pd.DataFrame({
        "合同申请年份": [2026],
        "明细运维开始开始日期": ["2026-01-10"],
        "明细运维结束日期": ["2026-01-01"],
        "产品金额": [100.0],
    })

    result = add_statistical_allocation_columns(
        df, config,
        "2025-01-01", "2025-12-31", "2026-01-01", "2026-12-31"
    )

    assert result.loc[0, "合同天数"] == -8
    assert result.loc[0, "去年按期分摊服务费"] == 0.0
    assert result.loc[0, "今年按期分摊服务费"] == 0.0
