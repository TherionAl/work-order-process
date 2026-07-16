from __future__ import annotations

import re

import pandas as pd


MONEY_KEYWORDS = ("金额", "价税", "单价", "回款", "开票", "确收", "应分摊")
MONEY_PATTERN = re.compile("|".join(MONEY_KEYWORDS))
DATE_FIELDS = [
    "合同申请日期",
    "归档日期",
    "明细运维开始开始日期",
    "明细运维结束日期",
]
NUMERIC_FIELDS = ["明细数量", "产品占比"]
TEXT_FIELDS = ["免费运维期（月）", "其他业务类型", "无效合同类型", "体系工程师"]
EXTRA_OLD_TEXT_COLUMNS = ["其他业务类型", "无效合同类型"]


def normalize_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


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
    numbers = pd.to_numeric(
        numeric_text.replace("/", ""), errors="coerce"
    ).fillna(0.0).astype("float64")
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


def build_contract_type(old_df: pd.DataFrame) -> pd.Series:
    standard_type = normalize_text(
        old_df.get("是否标准合同", pd.Series("", index=old_df.index))
    )
    return standard_type.where(
        standard_type == "统签散开合同", "普通销售合同"
    )


def build_yes_no_by_standard_type(
    old_df: pd.DataFrame, expected_value: str
) -> pd.Series:
    standard_type = normalize_text(
        old_df.get("是否标准合同", pd.Series("", index=old_df.index))
    )
    return pd.Series(
        ["是" if value == expected_value else "否" for value in standard_type],
        index=old_df.index,
    )


def build_business_type(old_df: pd.DataFrame) -> pd.Series:
    source = normalize_text(
        old_df.get("核算收入类型分组", pd.Series("", index=old_df.index))
    )
    type_mapping = {
        "运维服务": "运维服务费",
        "实施服务": "实施服务费",
        "SaaS及运营服务": "SAAS运营服务",
        "软件销售": "软件产品",
        "硬件及耗材销售": "其他硬件销售",
        "定制软件开发": "开发服务费",
    }
    return source.map(type_mapping).fillna("其他")


def build_contract_category(old_df: pd.DataFrame) -> pd.Series:
    contract_type = normalize_text(
        old_df.get("合同类型", pd.Series("", index=old_df.index))
    )
    return contract_type.map(
        {"运维合同": "运维合同", "实施合同": "非运维合同"}
    ).fillna("/")


def convert_old_to_new_columns(
    old_df: pd.DataFrame,
    output_columns: list[str],
    mapping: pd.Series,
    config: dict,
) -> pd.DataFrame:
    converted = pd.DataFrame(index=old_df.index)
    shared_amount_rules = {
        "去年同期收入金额": config["金额换算"]["去年收入基数字段"],
        "累计开票金额": config["金额换算"]["累计开票金额原字段"],
        "累计回款金额": config["金额换算"]["累计回款金额原字段"],
    }

    for target_column in output_columns:
        rule = str(mapping.get(target_column, "")).strip()
        if rule == "/":
            converted[target_column] = "/"
        elif rule == "旧ERP数据源统一为0":
            converted[target_column] = 0
        elif target_column == "合同类型" and "统签散开合同" in rule:
            converted[target_column] = build_contract_type(old_df)
        elif target_column == "暂估运维运营" and "运维收入暂估合同" in rule:
            converted[target_column] = build_yes_no_by_standard_type(
                old_df, "运维收入暂估合同"
            )
        elif target_column == "虚拟合同" and "虚拟销售合同" in rule:
            converted[target_column] = build_yes_no_by_standard_type(
                old_df, "虚拟销售合同"
            )
        elif target_column == "业务类型" and "核算收入类型分组" in rule:
            converted[target_column] = build_business_type(old_df)
        elif target_column == "合同分类" and "合同类型" in rule:
            converted[target_column] = build_contract_category(old_df)
        elif target_column == "营销平台" and rule in old_df.columns:
            converted[target_column] = normalize_platform(old_df[rule], config)
        elif target_column in shared_amount_rules:
            converted[target_column] = build_old_shared_amount(
                old_df, shared_amount_rules[target_column], config
            )
        elif rule in old_df.columns:
            converted[target_column] = old_df[rule]
        else:
            converted[target_column] = "/"
    return converted


def align_new_data(new_df: pd.DataFrame, output_columns: list[str]) -> pd.DataFrame:
    aligned = pd.DataFrame(index=new_df.index)
    for column in output_columns:
        if column in new_df.columns:
            aligned[column] = new_df[column]
        elif column in EXTRA_OLD_TEXT_COLUMNS:
            aligned[column] = ""
        elif MONEY_PATTERN.search(column):
            aligned[column] = 0.0
        else:
            aligned[column] = "/"
    return aligned


def normalize_money_columns(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column in result.columns:
        if MONEY_PATTERN.search(column):
            result[column] = parse_number_series(result[column])
    return result


def format_date_fields(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column in DATE_FIELDS:
        if column not in result.columns:
            continue
        text = normalize_text(result[column])
        parsed = pd.to_datetime(text, errors="coerce")
        result[column] = parsed.dt.strftime("%Y%m%d").fillna(text)
    return result


def format_numeric_fields(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column in NUMERIC_FIELDS:
        if column in result.columns:
            result[column] = pd.to_numeric(
                result[column].replace("/", pd.NA), errors="coerce"
            ).fillna(0.0)
    return result


def format_text_fields(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column in TEXT_FIELDS:
        if column in result.columns:
            result[column] = result[column].fillna("").astype(str)
    return result
