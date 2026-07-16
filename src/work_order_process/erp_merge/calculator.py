from __future__ import annotations

import pandas as pd

from .mapping import parse_number_series


def calculate_period_allocation(
    service_start: pd.Series,
    service_end: pd.Series,
    product_amount: pd.Series,
    contract_days: pd.Series,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> pd.Series:
    """按合同服务期与统计区间的重叠天数分摊产品金额"""
    period_start_values = pd.Series(period_start, index=service_start.index)
    period_end_values = pd.Series(period_end, index=service_start.index)
    overlap_start = service_start.where(service_start > period_start_values, period_start_values)
    overlap_end = service_end.where(service_end < period_end_values, period_end_values)
    overlap_days = ((overlap_end - overlap_start).dt.days + 1).clip(lower=0).fillna(0)

    valid_mask = service_start.notna() & service_end.notna() & (contract_days > 0)
    allocation = pd.Series(0.0, index=service_start.index)
    allocation.loc[valid_mask] = (
        product_amount.loc[valid_mask]
        * overlap_days.loc[valid_mask]
        / contract_days.loc[valid_mask]
    )
    return allocation


def add_statistical_allocation_columns(
    df: pd.DataFrame,
    config: dict,
    last_year_start: str,
    last_year_end: str,
    current_year_start: str,
    current_year_end: str,
) -> pd.DataFrame:
    """增加去年/今年统计区间和按期分摊服务费列"""
    result = df.copy()
    last_start = pd.Timestamp(last_year_start)
    last_end = pd.Timestamp(last_year_end)
    current_start = pd.Timestamp(current_year_start)
    current_end = pd.Timestamp(current_year_end)

    service_start = pd.to_datetime(
        result.get("明细运维开始开始日期", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip(),
        errors="coerce",
    )
    service_end = pd.to_datetime(
        result.get("明细运维结束日期", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip(),
        errors="coerce",
    )
    product_amount = parse_number_series(
        result.get("产品金额", pd.Series("", index=result.index))
    )
    contract_days = (service_end - service_start).dt.days.add(1).fillna(0)

    last_year_amount = calculate_period_allocation(
        service_start, service_end, product_amount, contract_days, last_start, last_end
    )
    current_year_amount = calculate_period_allocation(
        service_start, service_end, product_amount, contract_days, current_start, current_end
    )

    apply_year = pd.to_numeric(
        result.get("合同申请年份", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip(),
        errors="coerce",
    )
    current_apply_year = current_start.year
    backdated_to_current_mask = (apply_year == current_apply_year) & (last_year_amount > 0)

    result["合同天数"] = contract_days.astype("int64")
    result["去年统计起始日期"] = last_start
    result["去年统计截止日期"] = last_end
    result["去年按期分摊服务费"] = last_year_amount
    result["去年按期分摊服务费（去掉今年倒签的）"] = last_year_amount.mask(
        apply_year == current_apply_year, 0
    )
    result["今年统计起始日期"] = current_start
    result["今年统计截止日期"] = current_end
    result["今年按期分摊服务费"] = current_year_amount
    result["今年按期分摊服务费（加上倒签去年的服务费）"] = current_year_amount.mask(
        backdated_to_current_mask, current_year_amount + last_year_amount
    )
    return result
