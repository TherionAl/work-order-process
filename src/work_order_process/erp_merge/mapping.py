from __future__ import annotations

import pandas as pd


def normalize_platform(series: pd.Series, config: dict) -> pd.Series:
    """将旧ERP营销平台统一为新ERP口径"""
    platform_mapping = config.get("营销平台映射", {})
    normalized = series.fillna("").astype(str).str.strip()
    return normalized.map(platform_mapping).fillna(normalized)


def add_engineer_column(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """按营销平台匹配体系工程师，未匹配时保留空白"""
    engineer_mapping = config.get("体系工程师", {})
    result = df.copy()
    if "营销平台" not in df.columns:
        result["体系工程师"] = ""
        return result
    platform = df["营销平台"].fillna("").astype(str).str.strip()
    result["体系工程师"] = platform.map(engineer_mapping).fillna("")
    return result


def parse_number_series(series: pd.Series) -> pd.Series:
    """将金额或比例列转换为数值，兼容千分位逗号、百分号和空值"""
    text_values = series.fillna("").astype(str).str.strip()
    numeric_text = (
        text_values.str.replace(",", "", regex=False)
        .str.replace("，", "", regex=False)
        .str.rstrip("%")
    )
    numbers = pd.to_numeric(numeric_text.replace("/", ""), errors="coerce").fillna(0.0)
    percent_mask = text_values.str.endswith("%")
    numbers.loc[percent_mask] = numbers.loc[percent_mask] / 100
    return numbers


def build_old_shared_amount(old_df: pd.DataFrame, source_column: str, config: dict) -> pd.Series:
    """旧ERP指定金额字段按分成比例折算后导入"""
    amount = parse_number_series(old_df.get(source_column, pd.Series("", index=old_df.index)))
    share_ratio_col = config["金额换算"]["乘数因子字段"]
    share_ratio = parse_number_series(
        old_df.get(share_ratio_col, pd.Series("", index=old_df.index))
    )
    return amount * share_ratio
