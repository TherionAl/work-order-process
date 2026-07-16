r"""
合并新、旧 ERP 运维服务收入数据。

使用方式：
    在脚本所在目录下运行：
        uv run python merge_erp_data_20260703.py

脚本会自动完成以下工作：
1. 在当前目录查找两份 .xlsx 数据源文件；
2. 根据表头结构自动识别"新 ERP 主数据"和"旧 ERP 待补充数据"，不依赖文件名中的 new/old；
3. 自动查找文件名包含"新ERP与旧ERP数据合并字段对照关系"的规则文件，并按规则生成统一字段；
4. 读取完两份数据后，先用旧 ERP 的"合同分录ID"与新 ERP 的"企业版销售合同明细id"比对，
   如果旧 ERP 的合同分录ID已存在于新 ERP 企业版销售合同明细id中，则删除旧 ERP 对应行；
   如果不重复，则旧 ERP 的合同分录ID会按规则赋值到输出字段"标的行编码"；
5. 旧 ERP 中包含"合计"或"合计："的汇总行直接剔除；
6. 金额相关字段统一转为数值，缺失值填 0；
7. 增加"数据来源"列，标记每一行来自新 ERP 还是旧 ERP；
8. 增加"文件生成时间戳"列，格式为年月日时分秒，并放在最后一列；
9. 输出合并后的 xlsx 文件。
"""

from __future__ import annotations

import argparse
import logging
import re
import warnings
from datetime import datetime
from numbers import Real
from pathlib import Path
from time import perf_counter
from typing import Iterable

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

BASE_DIR = Path(__file__).resolve().parent
RULE_FILE_KEYWORD = "新ERP与旧ERP数据合并字段对照关系"
SOURCE_COLUMN = "数据来源"
TIMESTAMP_COLUMN = "文件生成时间戳"
SOURCE_DATE_COLUMN = "文件来源时间戳"
EXTRA_OLD_TEXT_COLUMNS = ["其他业务类型", "无效合同类型"]
PLATFORM_COLUMN = "营销平台"
ENGINEER_COLUMN = "体系工程师"
STATISTICAL_ALLOCATION_ANCHOR_COLUMN = "产品金额"
SERVICE_START_COLUMN = "明细运维开始开始日期"
SERVICE_END_COLUMN = "明细运维结束日期"
CONTRACT_APPLY_YEAR_COLUMN = "合同申请年份"
STATISTICAL_ALLOCATION_COLUMNS = [
    "合同天数",
    "去年统计起始日期",
    "去年统计截止日期",
    "去年按期分摊服务费",
    "去年按期分摊服务费（去掉今年倒签的）",
    "今年统计起始日期",
    "今年统计截止日期",
    "今年按期分摊服务费",
    "今年按期分摊服务费（加上倒签去年的服务费）",
]

# 扫描表头时最多检查的行数
MAX_HEADER_SCAN_ROWS = 10

_logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """配置日志输出格式与级别。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# 这些字段包含"金额/价税/单价/回款/开票/确收/应分摊"等财务含义。
# 旧 ERP 缺失时必须填 0，最后也统一转为数值，保证 Excel 中列类型尽量一致。
# 注意：不要把"收入"单独作为关键词，否则会误伤"是否一次性收入"这种是/否字段。
# ---------------------------------------------------------------------------
MONEY_KEYWORDS = ("金额", "价税", "单价", "回款", "开票", "确收", "应分摊")
# 预编译金额字段正则，避免逐列循环时重复做字符串 in 检查
_MONEY_PATTERN = re.compile("|".join(MONEY_KEYWORDS))

OLD_ERP_SHARE_RATIO_COLUMN = "分成比例"
OLD_ERP_SHARED_AMOUNT_RULES = {
    "去年同期收入金额": "累计收入金额-去年同期",
    "累计开票金额": "累计开票金额",
    "累计回款金额": "累计回款金额",
}

# 需要转换为日期字符串的字段（保留年月日，去掉时分秒）
# 注意：字段名要与实际输出列名完全一致
DATE_FIELDS = [
    "合同申请日期",          # 原值：2026-06-26 00:00:00 → 输出：20260626
    "归档日期",              # 原值：2026-06-23 09:05:42 → 输出：20260623
    "明细运维开始开始日期",  # 输出：20260623
    "明细运维结束日期",      # 输出：20260623
]

# 需要转换为数字的字段。
# "免费运维期（月）"要求保留源数据原样并以字符串形式输出，因此不放入数值转换列表。
NUMERIC_FIELDS = ["明细数量", "产品占比"]

# 需要强制保留为字符串的字段。
TEXT_FIELDS = ["免费运维期（月）", "其他业务类型", "无效合同类型", ENGINEER_COLUMN]

# 旧 ERP 营销平台需要先归并为新 ERP 口径，再参与后续合并和体系工程师匹配。
OLD_PLATFORM_RENAMES = {
    "海南分公司": "广西分公司",
    "浙江分公司": "山东分公司",
    "河南分公司": "北京分公司",
    "安徽分公司": "苏皖分公司",
    "江苏分公司": "苏皖分公司",
    "云南博思": "博思智合",
    "青海博思": "青海分公司",
}

# 体系工程师按最终营销平台匹配；未列入映射的平台保留为空白。
ENGINEER_BY_PLATFORM = {
    "博思智合": "黄迪",
    "广东瑞联": "黄迪",
    "广西分公司": "黄迪",
    "贵州分公司": "黄迪",
    "河北分公司": "黄迪",
    "深圳分公司": "黄迪",
    "西藏分公司": "黄迪",
    "北京分公司": "黄微",
    "山西分公司": "黄微",
    "四川分公司": "黄微",
    "苏皖分公司": "黄微",
    "总部大区": "黄微",
    "黑龙江博思": "李金艳",
    "湖南分公司": "李金艳",
    "江西分公司": "李金艳",
    "辽宁分公司": "李金艳",
    "厦门分公司": "李金艳",
    "山东分公司": "李金艳",
    "甘肃分公司": "苏远星",
    "湖北博思": "苏远星",
    "吉林分公司": "苏远星",
    "青海分公司": "苏远星",
    "陕西分公司": "苏远星",
    "中央": "苏远星",
    "重庆分公司": "苏远星",
    "内蒙古金财": "庄明霞",
    "宁夏分公司": "庄明霞",
    "上海分公司": "庄明霞",
    "天津分公司": "庄明霞",
    "新疆分公司": "庄明霞",
}


def is_missing_scalar(value: object) -> bool:
    """判断单个单元格值是否为空，避免 pd.isna 在类型检查中被推断为数组结果。"""
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    if isinstance(value, Real):
        try:
            return value != value
        except TypeError:
            return False
    return False


def clean_header(value: object) -> str:
    """清洗列名：去除空白，将 Excel 单元格中的换行统一保留为普通换行。"""
    if is_missing_scalar(value):
        return ""
    return str(value).strip()


def normalize_text(series: pd.Series) -> pd.Series:
    """把空值转为空字符串，并去掉首尾空白，便于后续字段匹配和规则判断。"""
    return series.fillna("").astype(str).str.strip()


def print_elapsed(label: str, start_time: float) -> float:
    """打印阶段耗时，并返回新的计时起点。"""
    now = perf_counter()
    _logger.info("%s，用时：%.1f 秒", label, now - start_time)
    return now


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """解析统计区间参数；未传时后续按脚本运行年份自动生成默认区间。"""
    parser = argparse.ArgumentParser(description="合并新旧 ERP 数据并计算年度分摊服务费。")
    parser.add_argument("--last-year-start", help="去年统计起始日期，例如 20250101 或 2025-01-01")
    parser.add_argument("--last-year-end", help="去年统计截止日期，例如 20251231 或 2025-12-31")
    parser.add_argument("--current-year-start", help="今年统计起始日期，例如 20260101 或 2026-01-01")
    parser.add_argument("--current-year-end", help="今年统计截止日期，例如 20261231 或 2026-12-31")
    return parser.parse_args(list(argv) if argv is not None else None)


def parse_statistical_date(value: object, option_name: str) -> pd.Timestamp:
    """将统计区间参数解析为日期。"""
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{option_name} 参数不是有效日期：{value}")
    return pd.Timestamp(parsed).normalize()


def resolve_statistical_periods(
    args: argparse.Namespace,
    generated_at: datetime,
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """按参数或默认自然年生成去年/今年统计区间。"""
    current_year = generated_at.year
    last_year = current_year - 1
    last_year_start = parse_statistical_date(
        args.last_year_start or f"{last_year}-01-01",
        "--last-year-start",
    )
    last_year_end = parse_statistical_date(
        args.last_year_end or f"{last_year}-12-31",
        "--last-year-end",
    )
    current_year_start = parse_statistical_date(
        args.current_year_start or f"{current_year}-01-01",
        "--current-year-start",
    )
    current_year_end = parse_statistical_date(
        args.current_year_end or f"{current_year}-12-31",
        "--current-year-end",
    )

    if last_year_start > last_year_end:
        raise ValueError("去年统计起始日期不能晚于去年统计截止日期。")
    if current_year_start > current_year_end:
        raise ValueError("今年统计起始日期不能晚于今年统计截止日期。")

    return last_year_start, last_year_end, current_year_start, current_year_end


def find_header_row(raw: pd.DataFrame, required_columns: Iterable[str]) -> int:
    """
    在没有固定表头行的数据中定位真实表头。

    两份源文件的表头行不一致：
    - 新 ERP 主数据的表头在第 2 行左右，字段类似"合同编号、销售组织、签约客户"；
    - 旧 ERP 数据源有多层表头，真实字段在第 3 行左右，字段类似"唯一ID、合同分录ID、签署公司"。
    因此这里不写死行号，而是查找同时包含关键字段的行。
    """
    required = set(required_columns)
    for row_index in range(min(MAX_HEADER_SCAN_ROWS, len(raw))):
        row_values = {clean_header(value) for value in raw.iloc[row_index].tolist()}
        if required.issubset(row_values):
            return row_index
    raise ValueError(f"无法定位包含字段 {sorted(required)} 的表头行，请检查源文件格式。")


def _read_raw_excel(file_path: Path) -> pd.DataFrame:
    """读取 Excel 原始数据（全部按字符串处理），不做任何解析。"""
    return pd.read_excel(file_path, header=None, dtype=str, keep_default_na=False)


def read_table_with_detected_header(
    file_path: Path,
    required_columns: Iterable[str],
) -> pd.DataFrame:
    """
    读取 Excel，并基于关键字段自动识别表头行。

    读取时全部先按字符串处理，避免合同编号、明细 ID 等字段被 Excel 或 pandas
    自动转成科学计数法或浮点数。金额字段会在合并后单独转为数值。
    """
    raw = _read_raw_excel(file_path)
    header_row = find_header_row(raw, required_columns)
    columns = [clean_header(value) for value in raw.iloc[header_row].tolist()]
    data = raw.iloc[header_row + 1 :].copy()
    data.columns = columns

    # 如果导出的 Excel 存在空列名，保留也无意义，直接删除。
    # 这一步要放在判断"合计："行之前，因为旧 ERP 多层表头中可能有多个空列名。
    data = data.loc[:, [column != "" for column in data.columns]]

    # 删除完全空白行，以及常见的"合计"汇总行（首列模糊匹配）。
    data = data.replace("", pd.NA).dropna(how="all").fillna("")
    first_col = data.columns[0]
    data = data[~data[first_col].map(_is_total_cell)].copy()
    return data.reset_index(drop=True)


def find_rule_file() -> Path:
    """
    自动查找字段对照规则文件。

    规则文件版本会随日期更新，因此不再写死完整文件名，只要求：
    - 文件名包含"新ERP与旧ERP数据合并字段对照关系"；
    - 扩展名为 .xlsx；
    - 不是 Excel 打开的临时文件（~$ 开头）。

    如果目录中存在多个版本，优先使用最后修改时间最新的一份。
    """
    candidates = [
        path
        for path in BASE_DIR.glob("*.xlsx")
        if RULE_FILE_KEYWORD in path.name and not path.name.startswith("~$")
    ]
    if not candidates:
        raise FileNotFoundError(f"当前目录下未找到文件名包含“{RULE_FILE_KEYWORD}”的规则文件。")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _normalize_for_total_check(value: object) -> str:
    """
    把任意单元格值归一化，用于"合计"汇总行的模糊匹配。

    实际导出文件中，"合计"汇总行的文本常见以下变体：
    - 全角/半角冒号："合计："、"合计:"
    - 尾部附带空白或不可见字符
    - 被 Excel 导出的前后空格

    归一化规则：转字符串 → strip → 去掉末尾全角/半角冒号，
    最终只要裸文本等于"合计"即视为汇总行标记。
    """
    text = str(value).strip()
    return text.rstrip("：:")


def _is_total_cell(value: object) -> bool:
    """判断单个单元格是否是"合计"汇总行标记（模糊匹配）。"""
    if is_missing_scalar(value):
        return False
    return _normalize_for_total_check(value) == "合计"


def drop_total_rows(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """
    删除源数据中的合计汇总行，并返回过滤后的 DataFrame。

    清理细则（旧 ERP / 新 ERP 均适用）：
    1. 遍历每一行的全部（字符串型）单元格；
    2. 对每个单元格做归一化：去首尾空白、去掉尾部的全角/半角冒号；
    3. 归一化后文本 == "合计" 即视为汇总行标记；
    4. 任一单元格命中，整行剔除；
    5. 被删除的行不参与后续去重、字段映射与最终合并。

    典型可识别的变体包括："合计"、"合计："、"合计:"、" 合计 "。
    """
    before_count = len(df)
    # 对全部列做归一化检查（避免 select_dtypes 在不同 pandas 版本下漏检 str 列）
    bool_df = df.map(_is_total_cell)
    total_row_mask = bool_df.any(axis=1)
    df = df[~total_row_mask].copy()
    dropped_count = before_count - len(df)
    if dropped_count:
        _logger.info("%s 剔除合计行数：%d", source_name, dropped_count)
    else:
        _logger.info("%s 未发现合计汇总行", source_name)
    return df.reset_index(drop=True)


def identify_source_files() -> tuple[Path, Path]:
    """
    自动识别新旧 ERP 数据源。

    本批文件的文件名可能会随导出批次变化，所以用字段识别：
    - 新 ERP 主数据：包含"合同编号、销售组织、签约客户、标的行编码"等目标结构字段；
    - 旧 ERP 待转换数据：包含"合同编号、签署公司、合同分录ID、是否标准合同"等旧结构字段。
    """
    candidates = [
        path
        for path in BASE_DIR.glob("*.xlsx")
        if RULE_FILE_KEYWORD not in path.name
        and not path.name.startswith("~$")
        and not path.name.startswith("新旧ERP数据合并结果")
    ]
    if len(candidates) < 2:
        raise FileNotFoundError("当前目录下未找到两份可识别的 .xlsx 数据源文件。")

    new_file: Path | None = None
    old_file: Path | None = None
    for path in candidates:
        raw_preview = _read_raw_excel(path).iloc[:MAX_HEADER_SCAN_ROWS]
        preview_values = {
            clean_header(value)
            for value in raw_preview.to_numpy().ravel().tolist()
            if clean_header(value)
        }
        if {"合同编号", "销售组织", "签约客户", "标的行编码"}.issubset(preview_values):
            if new_file is not None:
                raise ValueError(
                    f"检测到多个文件匹配新 ERP 主数据特征：{new_file.name} 与 {path.name}"
                )
            new_file = path
        if {"合同编号", "签署公司", "合同分录ID", "是否标准合同"}.issubset(preview_values):
            if old_file is not None:
                raise ValueError(
                    f"检测到多个文件匹配旧 ERP 数据源特征：{old_file.name} 与 {path.name}"
                )
            old_file = path

    if new_file is None or old_file is None:
        raise ValueError("无法根据表头自动识别新 ERP 主数据或旧 ERP 数据源，请检查源文件字段。")
    return new_file, old_file


def load_rules() -> tuple[list[str], pd.Series]:
    """
    读取字段对照表。

    对照表第一列"新ERP字段"是说明列，不属于最终业务字段。
    从第二列"序号"开始才是最终输出的新 ERP 字段；第一行是旧 ERP 字段名或加工规则；
    第二行是样例数据，不参与规则计算。
    """
    rule_file = find_rule_file()
    _logger.info("识别到字段对照规则文件：%s", rule_file.name)
    rules = pd.read_excel(rule_file, dtype=str, keep_default_na=False)
    output_columns = [clean_header(column) for column in rules.columns[1:]]
    mapping = rules.iloc[0, 1:].copy()
    mapping.index = output_columns

    if ENGINEER_COLUMN not in output_columns:
        if PLATFORM_COLUMN in output_columns:
            insert_index = output_columns.index(PLATFORM_COLUMN) + 1
            output_columns.insert(insert_index, ENGINEER_COLUMN)
        else:
            output_columns.append(ENGINEER_COLUMN)
        mapping.loc[ENGINEER_COLUMN] = ""

    # 规则表之外额外追加的旧 ERP 文本字段：
    # - 新 ERP 没有这两列，后续 align_new_data 会自动留空；
    # - 旧 ERP 保留原字段内容，后续 convert_old_to_new_columns 会按同名字段直接复制。
    for column in EXTRA_OLD_TEXT_COLUMNS:
        if column not in output_columns:
            output_columns.append(column)
            mapping.loc[column] = column

    return output_columns, mapping


def build_contract_type(old_df: pd.DataFrame) -> pd.Series:
    """旧 ERP 的"是否标准合同"为"统签散开合同"时，合同类型写"统签散开合同"，否则写"普通销售合同"。"""
    standard_type = normalize_text(old_df.get("是否标准合同", pd.Series("", index=old_df.index)))
    return pd.Series(
        np.where(standard_type == "统签散开合同", "统签散开合同", "普通销售合同"),
        index=old_df.index,
    )


def build_yes_no_by_standard_type(old_df: pd.DataFrame, expected_value: str) -> pd.Series:
    """按"是否标准合同"生成"是/否"字段，例如暂估运维运营、虚拟合同。"""
    standard_type = normalize_text(old_df.get("是否标准合同", pd.Series("", index=old_df.index)))
    return pd.Series(np.where(standard_type == expected_value, "是", "否"), index=old_df.index)


def build_business_type(old_df: pd.DataFrame) -> pd.Series:
    """按"核算收入类型分组"转换为新 ERP 的业务类型。"""
    source = normalize_text(old_df.get("核算收入类型分组", pd.Series("", index=old_df.index)))
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
    """按旧 ERP 的"合同类型"生成新 ERP 的"合同分类"。"""
    contract_type = normalize_text(old_df.get("合同类型", pd.Series("", index=old_df.index)))
    category_mapping = {
        "运维合同": "运维合同",
        "实施合同": "非运维合同",
    }
    return contract_type.map(category_mapping).fillna("/")


def normalize_old_platform(series: pd.Series) -> pd.Series:
    """将旧 ERP 营销平台统一为新 ERP 口径。"""
    platform = normalize_text(series)
    return platform.map(OLD_PLATFORM_RENAMES).fillna(platform)


def parse_number_series(series: pd.Series) -> pd.Series:
    """将金额或比例列转换为数值，兼容千分位逗号、百分号和空值。"""
    text_values = normalize_text(series)
    numeric_text = (
        text_values.str.replace(",", "", regex=False)
        .str.replace("，", "", regex=False)
        .str.rstrip("%")
    )
    numbers = pd.to_numeric(numeric_text.replace("/", ""), errors="coerce").fillna(0.0)
    percent_mask = text_values.str.endswith("%")
    numbers.loc[percent_mask] = numbers.loc[percent_mask] / 100
    return numbers


def build_old_shared_amount(old_df: pd.DataFrame, source_column: str) -> pd.Series:
    """旧 ERP 指定金额字段按分成比例折算后导入。"""
    amount = parse_number_series(old_df.get(source_column, pd.Series("", index=old_df.index)))
    share_ratio = parse_number_series(
        old_df.get(OLD_ERP_SHARE_RATIO_COLUMN, pd.Series("", index=old_df.index))
    )
    return amount * share_ratio


def add_engineer_column(df: pd.DataFrame) -> pd.DataFrame:
    """按最终营销平台匹配体系工程师，未匹配时保留空白。"""
    if PLATFORM_COLUMN not in df.columns:
        df[ENGINEER_COLUMN] = ""
        return df

    platform = normalize_text(df[PLATFORM_COLUMN])
    df[ENGINEER_COLUMN] = platform.map(ENGINEER_BY_PLATFORM).fillna("")
    return df


def convert_old_to_new_columns(
    old_df: pd.DataFrame,
    output_columns: list[str],
    mapping: pd.Series,
) -> pd.DataFrame:
    """
    将旧 ERP 数据转换成新 ERP 字段结构。

    规则来源：
    - 映射值为"/"：旧 ERP 没有该字段，统一填"/"；
    - 映射值为旧 ERP 字段名：直接复制；
    - 映射值为"旧ERP数据源统一为0"：统一填 0；
    - 映射值为文字规则：按规则函数加工。
    """
    converted = pd.DataFrame(index=old_df.index)
    # 预先解析每条规则，避免循环内重复调用 clean_header
    parsed_rules = {col: clean_header(mapping[col]) for col in output_columns}

    for target_column, rule in parsed_rules.items():
        if rule == "/":
            converted[target_column] = "/"
        elif rule == "旧ERP数据源统一为0":
            converted[target_column] = 0
        elif target_column == "合同类型" and "统签散开合同" in rule:
            converted[target_column] = build_contract_type(old_df)
        elif target_column == "暂估运维运营" and "运维收入暂估合同" in rule:
            converted[target_column] = build_yes_no_by_standard_type(old_df, "运维收入暂估合同")
        elif target_column == "虚拟合同" and "虚拟销售合同" in rule:
            converted[target_column] = build_yes_no_by_standard_type(old_df, "虚拟销售合同")
        elif target_column == "业务类型" and "核算收入类型分组" in rule:
            converted[target_column] = build_business_type(old_df)
        elif target_column == "合同分类" and "合同类型" in rule:
            converted[target_column] = build_contract_category(old_df)
        elif target_column == PLATFORM_COLUMN and rule in old_df.columns:
            converted[target_column] = normalize_old_platform(old_df[rule])
        elif target_column in OLD_ERP_SHARED_AMOUNT_RULES:
            converted[target_column] = build_old_shared_amount(
                old_df, OLD_ERP_SHARED_AMOUNT_RULES[target_column]
            )
        elif rule in old_df.columns:
            converted[target_column] = old_df[rule]
        else:
            # 规则表中未能直接识别的内容，先按无来源字段处理，避免脚本中断。
            # 如果后续规则表增加新加工逻辑，可在这里补充对应分支。
            converted[target_column] = "/"

    return converted


def align_new_data(new_df: pd.DataFrame, output_columns: list[str]) -> pd.DataFrame:
    """
    将新 ERP 主数据调整为输出字段顺序。

    新 ERP 主数据已有的同名字段保持不变；如果目标字段在源表不存在：
    - 金额相关字段填 0；
    - 其他业务类型、无效合同类型留空；
    - 非金额字段填"/"。
    """
    aligned = pd.DataFrame(index=new_df.index)
    for column in output_columns:
        if column in new_df.columns:
            aligned[column] = new_df[column]
        elif column in EXTRA_OLD_TEXT_COLUMNS:
            aligned[column] = ""
        elif _MONEY_PATTERN.search(column):
            aligned[column] = 0
        else:
            aligned[column] = "/"
    return aligned


def normalize_money_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    金额相关字段缺失填 0，并统一转为数值。

    pandas 的 to_numeric 会把无法解析的内容转为 NaN，再用 0 填充。
    这样可以避免同一列中既有字符串"/"又有数字，影响后续透视或汇总。
    """
    for column in df.columns:
        if _MONEY_PATTERN.search(column):
            df[column] = pd.to_numeric(df[column].replace("/", 0), errors="coerce").fillna(0)
    return df


def format_date_fields(df: pd.DataFrame) -> pd.DataFrame:
    """将指定日期字段格式化为 YYYYMMDD 字符串。"""
    for column in DATE_FIELDS:
        if column not in df.columns:
            continue
        text_values = normalize_text(df[column])
        parsed_dates = pd.to_datetime(text_values, errors="coerce")
        formatted_dates = parsed_dates.dt.strftime("%Y%m%d")
        df[column] = formatted_dates.fillna(text_values)
        # 打印转换统计信息，便于调试
        non_empty_count = (df[column] != "").sum()
        if non_empty_count > 0:
            _logger.info("  已转换字段 '%s'，非空值数量：%d", column, non_empty_count)
    return df


def format_numeric_fields(df: pd.DataFrame) -> pd.DataFrame:
    """
    将指定字段转换为数字类型。

    - 明细数量：转换为 float（浮点数）
    - 产品占比：转换为 float（浮点数）
    """
    for column in NUMERIC_FIELDS:
        if column not in df.columns:
            continue

        # 先将空值或"/"替换为 NaN
        series = df[column].replace("/", pd.NA)
        # 转换为数值，无法转换的设为 NaN
        series = pd.to_numeric(series, errors="coerce")

        # 明细数量 和 产品占比 转换为 float。
        df[column] = series.astype("float64").fillna(0.0)
        _logger.info("  已转换字段 '%s' 为 float 类型", column)

    return df


def format_text_fields(df: pd.DataFrame) -> pd.DataFrame:
    """将指定字段按字符串保存，不做数值化、补 0 或其他特殊处理。"""
    for column in TEXT_FIELDS:
        if column in df.columns:
            df[column] = df[column].fillna("").astype(str)
            _logger.info("  已保留字段 '%s' 为字符串类型", column)
    return df


def calculate_period_allocation(
    service_start: pd.Series,
    service_end: pd.Series,
    product_amount: pd.Series,
    contract_days: pd.Series,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> pd.Series:
    """按合同服务期与统计区间的重叠天数分摊产品金额。"""
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


def insert_columns_after(
    df: pd.DataFrame,
    anchor_column: str,
    inserted_columns: list[str],
) -> pd.DataFrame:
    """将新增列移动到指定锚点列之后；锚点不存在时保留在当前尾部。"""
    base_columns = [column for column in df.columns if column not in inserted_columns]
    if anchor_column in base_columns:
        insert_index = base_columns.index(anchor_column) + 1
    else:
        insert_index = len(base_columns)
    ordered_columns = (
        base_columns[:insert_index] + inserted_columns + base_columns[insert_index:]
    )
    return df[ordered_columns]


def add_statistical_allocation_columns(
    df: pd.DataFrame,
    last_year_start: object,
    last_year_end: object,
    current_year_start: object,
    current_year_end: object,
) -> pd.DataFrame:
    """增加去年/今年统计区间和按期分摊服务费列。"""
    result = df.copy()
    last_start = parse_statistical_date(last_year_start, "去年统计起始日期")
    last_end = parse_statistical_date(last_year_end, "去年统计截止日期")
    current_start = parse_statistical_date(current_year_start, "今年统计起始日期")
    current_end = parse_statistical_date(current_year_end, "今年统计截止日期")

    service_start = pd.to_datetime(
        normalize_text(result.get(SERVICE_START_COLUMN, pd.Series("", index=result.index))),
        errors="coerce",
    )
    service_end = pd.to_datetime(
        normalize_text(result.get(SERVICE_END_COLUMN, pd.Series("", index=result.index))),
        errors="coerce",
    )
    product_amount = parse_number_series(
        result.get(STATISTICAL_ALLOCATION_ANCHOR_COLUMN, pd.Series("", index=result.index))
    )
    contract_days = ((service_end - service_start).dt.days + 1).clip(lower=0).fillna(0)

    last_year_amount = calculate_period_allocation(
        service_start,
        service_end,
        product_amount,
        contract_days,
        last_start,
        last_end,
    )
    current_year_amount = calculate_period_allocation(
        service_start,
        service_end,
        product_amount,
        contract_days,
        current_start,
        current_end,
    )

    apply_year = pd.to_numeric(
        normalize_text(result.get(CONTRACT_APPLY_YEAR_COLUMN, pd.Series("", index=result.index))),
        errors="coerce",
    )
    current_apply_year = current_start.year
    backdated_to_current_mask = (apply_year == current_apply_year) & (last_year_amount > 0)

    result["合同天数"] = contract_days.astype("int64")
    result["去年统计起始日期"] = last_start
    result["去年统计截止日期"] = last_end
    result["去年按期分摊服务费"] = last_year_amount
    result["去年按期分摊服务费（去掉今年倒签的）"] = last_year_amount.mask(
        apply_year == current_apply_year,
        0,
    )
    result["今年统计起始日期"] = current_start
    result["今年统计截止日期"] = current_end
    result["今年按期分摊服务费"] = current_year_amount
    result["今年按期分摊服务费（加上倒签去年的服务费）"] = current_year_amount.mask(
        backdated_to_current_mask,
        current_year_amount + last_year_amount,
    )

    return insert_columns_after(
        result,
        STATISTICAL_ALLOCATION_ANCHOR_COLUMN,
        STATISTICAL_ALLOCATION_COLUMNS,
    )


def build_document_version(df: pd.DataFrame) -> pd.DataFrame:
    """
    生成文档版数据副本。

    原始输出文件仍保持现有规则：日期字段为 YYYYMMDD 文本。
    文档版单独将日期相关字段转换为 Excel 可识别的日期类型，便于在 Word/Excel 文档中展示和筛选。
    """
    document_df = df.copy()
    for column in DATE_FIELDS:
        if column not in document_df.columns:
            continue

        text_values = normalize_text(document_df[column])
        parsed_dates = pd.to_datetime(text_values, format="%Y%m%d", errors="coerce")

        # 兼容极少数没有被 format_date_fields 转成 YYYYMMDD 的日期字符串。
        fallback_dates = pd.to_datetime(text_values, errors="coerce")
        document_df[column] = parsed_dates.fillna(fallback_dates)

    return document_df


def normalize_excel_cell_value(value: object) -> object:
    """将 pandas/numpy 标量转换为 openpyxl 可写入的普通 Python 值。"""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def write_dataframe_streaming_excel(
    df: pd.DataFrame,
    output_file: Path,
    date_columns: Iterable[str] = (),
) -> None:
    """用 openpyxl 流式写出大表，避免 pandas 普通写法占用过高内存。"""
    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet("Sheet1")
    headers = list(df.columns)
    worksheet.append(headers)

    date_column_indexes = {
        headers.index(column)
        for column in date_columns
        if column in headers
    }

    for row_values in df.itertuples(index=False, name=None):
        excel_row = []
        for column_index, value in enumerate(row_values):
            normalized_value = normalize_excel_cell_value(value)
            if column_index in date_column_indexes and normalized_value is not None:
                cell = WriteOnlyCell(worksheet, value=normalized_value)
                cell.number_format = "yyyy-mm-dd"
                excel_row.append(cell)
            else:
                excel_row.append(normalized_value)
        worksheet.append(excel_row)

    workbook.save(output_file)


def write_document_version_excel(df: pd.DataFrame, output_file: Path) -> None:
    """写出文档版 Excel，并将日期相关字段设置为真正的 Excel 日期格式。"""
    write_dataframe_streaming_excel(
        df,
        output_file,
        date_columns=[
            *DATE_FIELDS,
            "去年统计起始日期",
            "去年统计截止日期",
            "今年统计起始日期",
            "今年统计截止日期",
        ],
    )


def extract_source_date_timestamp(files: Iterable[Path]) -> str:
    """
    从源文件名中提取日期并转换为 YYYYMMDD 字符串。

    遍历所有源文件，优先使用第一个匹配到日期的文件。
    如果多个文件都有日期，保留最新的一个。
    如果文件名中没有匹配到日期，则返回空字符串，避免误填脚本运行日期。
    """
    latest_date = ""
    for file_path in files:
        match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", file_path.name)
        if match:
            year, month, day = match.groups()
            date_str = f"{int(year):04d}{int(month):02d}{int(day):02d}"
            if date_str > latest_date:
                latest_date = date_str
    return latest_date


def add_file_timestamp_column(df: pd.DataFrame, generated_at: datetime) -> pd.DataFrame:
    """增加文件生成时间戳列，所有行使用同一个生成时间。"""
    df[TIMESTAMP_COLUMN] = generated_at.strftime("%Y%m%d%H%M%S")
    return df


def add_source_date_column(df: pd.DataFrame, source_date_timestamp: str) -> pd.DataFrame:
    """增加文件来源时间戳列，所有行使用源文件名中的日期。"""
    df[SOURCE_DATE_COLUMN] = source_date_timestamp
    return df


def reorder_output_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    调整最终输出列顺序。

    用户指定"文件来源时间戳"作为最后一列。
    其他业务字段保持规则表中的原始顺序，"数据来源"和"文件生成时间戳"放在来源时间戳之前。
    """
    middle_columns = [
        column
        for column in df.columns
        if column not in (SOURCE_COLUMN, TIMESTAMP_COLUMN, SOURCE_DATE_COLUMN)
    ]
    return df[middle_columns + [SOURCE_COLUMN, TIMESTAMP_COLUMN, SOURCE_DATE_COLUMN]]


def remove_old_rows_existing_in_new_by_line_id(
    old_df: pd.DataFrame,
    new_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    按明细行维度过滤旧 ERP 数据。

    新需求不再按"合同编号"判断旧 ERP 是否追加，而是先比较：
    - 旧 ERP："合同分录ID"
    - 新 ERP："企业版销售合同明细id"

    如果旧 ERP 的合同分录ID已经出现在新 ERP 的企业版销售合同明细id中，说明这条旧 ERP 明细
    已在新 ERP 主数据中存在，应先从旧 ERP 数据中删除，再执行字段映射、格式转换和合并。
    未重复的旧 ERP 行会在 convert_old_to_new_columns 中按规则表映射：
    输出字段"标的行编码" = 旧 ERP "合同分录ID"。
    """
    old_key = "合同分录ID"
    new_key = "企业版销售合同明细id"
    if old_key not in old_df.columns:
        raise KeyError(f'旧 ERP 数据源缺少"{old_key}"字段，无法按明细行比对。')
    if new_key not in new_df.columns:
        raise KeyError(f'新 ERP 主数据缺少"{new_key}"字段，无法按明细行比对。')

    new_line_ids = set(normalize_text(new_df[new_key]))
    new_line_ids.discard("")

    old_line_ids = normalize_text(old_df[old_key])
    before_count = len(old_df)
    filtered_old_df = old_df[~old_line_ids.isin(new_line_ids)].copy()
    removed_count = before_count - len(filtered_old_df)

    _logger.info("旧 ERP 按合同分录ID匹配新 ERP 企业版销售合同明细id删除行数：%d", removed_count)
    return filtered_old_df


def main(argv: Iterable[str] | None = None) -> None:
    _setup_logging()
    warnings.filterwarnings("ignore", message="Workbook contains no default style")

    total_start = perf_counter()
    stage_start = total_start

    _logger.info("开始执行新旧 ERP 数据合并...")
    args = parse_args(argv)
    output_columns, mapping = load_rules()
    new_file, old_file = identify_source_files()
    generated_at = datetime.now()
    last_year_start, last_year_end, current_year_start, current_year_end = resolve_statistical_periods(
        args,
        generated_at,
    )
    source_date_timestamp = extract_source_date_timestamp([new_file, old_file])
    stage_start = print_elapsed("完成规则文件和数据源识别", stage_start)

    _logger.info("识别到新 ERP 主数据文件：%s", new_file.name)
    _logger.info("识别到旧 ERP 待合并文件：%s", old_file.name)
    _logger.info(
        "统计区间：去年 %s 至 %s；今年 %s 至 %s",
        last_year_start.strftime("%Y-%m-%d"),
        last_year_end.strftime("%Y-%m-%d"),
        current_year_start.strftime("%Y-%m-%d"),
        current_year_end.strftime("%Y-%m-%d"),
    )
    _logger.info("开始格式转换...")

    _logger.info("正在读取新 ERP 主数据...")
    new_df = read_table_with_detected_header(
        new_file, ["合同编号", "销售组织", "签约客户", "标的行编码"]
    )
    stage_start = print_elapsed(f"完成新 ERP 读取，行数：{len(new_df)}", stage_start)

    _logger.info("正在读取旧 ERP 数据源...")
    old_df = read_table_with_detected_header(
        old_file, ["合同编号", "签署公司", "合同分录ID", "是否标准合同"]
    )
    stage_start = print_elapsed(f"完成旧 ERP 读取，行数：{len(old_df)}", stage_start)

    new_df = drop_total_rows(new_df, "新 ERP 主数据")
    old_df = drop_total_rows(old_df, "旧 ERP 数据源")
    old_rows_before_line_id_filter = len(old_df)
    old_df = remove_old_rows_existing_in_new_by_line_id(old_df, new_df)
    stage_start = print_elapsed(
        f"完成旧 ERP 合计行和重复明细过滤，剩余行数：{len(old_df)}", stage_start
    )

    new_aligned = align_new_data(new_df, output_columns)
    old_converted = convert_old_to_new_columns(old_df, output_columns, mapping)
    new_aligned[SOURCE_COLUMN] = "新ERP"
    old_converted[SOURCE_COLUMN] = "旧ERP"
    stage_start = print_elapsed("完成新旧 ERP 字段映射", stage_start)

    # 新需求下不再按合同编号过滤旧 ERP；旧 ERP 只做上面的合同分录ID/企业版销售合同明细id过滤。
    merged = pd.concat([new_aligned, old_converted], ignore_index=True)
    merged = add_engineer_column(merged)
    stage_start = print_elapsed(f"完成数据拼接，合并后行数：{len(merged)}", stage_start)

    _logger.info("--- 金额字段转换 ---")
    merged = normalize_money_columns(merged)
    stage_start = print_elapsed("完成金额字段转换", stage_start)

    _logger.info("--- 日期字段转换 ---")
    merged = format_date_fields(merged)
    stage_start = print_elapsed("完成日期字段转换", stage_start)

    _logger.info("--- 数值字段转换 ---")
    merged = format_numeric_fields(merged)
    stage_start = print_elapsed("完成数值字段转换", stage_start)

    _logger.info("--- 字符串字段保留 ---")
    merged = format_text_fields(merged)

    _logger.info("--- 去年/今年按期分摊服务费计算 ---")
    merged = add_statistical_allocation_columns(
        merged,
        last_year_start,
        last_year_end,
        current_year_start,
        current_year_end,
    )
    stage_start = print_elapsed("完成去年/今年按期分摊服务费计算", stage_start)

    merged = add_file_timestamp_column(merged, generated_at)
    merged = add_source_date_column(merged, source_date_timestamp)

    # 删除合同编号为空的行（空值、纯空白、"/" 均视为无合同编号）。
    if "合同编号" in merged.columns:
        before_count = len(merged)
        contract_no = normalize_text(merged["合同编号"])
        merged = merged[~contract_no.isin({"", "/"})].copy()
        dropped_count = before_count - len(merged)
        _logger.info("删除合同编号为空的行数：%d", dropped_count)
        merged = merged.reset_index(drop=True)

    # 重新生成序号，避免两份数据原始序号重复。
    if "序号" in merged.columns:
        merged["序号"] = range(1, len(merged) + 1)
    merged = reorder_output_columns(merged)

    # 出 Excel 前补齐两列空白：
    # - 无效合同类型：空白视为"有效"；
    # - 其他业务类型：空白视为"非税票据"。
    if "无效合同类型" in merged.columns:
        before_count = merged["无效合同类型"].isna().sum() + (merged["无效合同类型"] == "").sum()
        merged["无效合同类型"] = (
            merged["无效合同类型"].fillna("").astype(str).str.strip().replace("", "有效")
        )
        _logger.info("无效合同类型空白填充为“有效”的行数：%d", before_count)

    if "其他业务类型" in merged.columns:
        before_count = merged["其他业务类型"].isna().sum() + (merged["其他业务类型"] == "").sum()
        merged["其他业务类型"] = (
            merged["其他业务类型"].fillna("").astype(str).str.strip().replace("", "非税票据")
        )
        _logger.info("其他业务类型空白填充为“非税票据”的行数：%d", before_count)

    # 输出文件名加上时间戳
    timestamp_str = generated_at.strftime("%Y%m%d%H%M")
    output_file = BASE_DIR / f"新旧ERP数据合并结果_{timestamp_str}.xlsx"
    document_output_file = BASE_DIR / f"新旧ERP数据合并结果文档版_{timestamp_str}.xlsx"
    _logger.info("正在写出普通版 Excel...")
    write_dataframe_streaming_excel(
        merged,
        output_file,
        date_columns=[
            "去年统计起始日期",
            "去年统计截止日期",
            "今年统计起始日期",
            "今年统计截止日期",
        ],
    )
    stage_start = print_elapsed("完成普通版 Excel 写出", stage_start)

    _logger.info("正在生成并写出文档版 Excel...")
    document_merged = build_document_version(merged)
    write_document_version_excel(document_merged, document_output_file)
    stage_start = print_elapsed("完成文档版 Excel 写出", stage_start)

    _logger.info("=" * 50)
    _logger.info("新 ERP 主数据行数：%d", len(new_aligned))
    _logger.info("旧 ERP 明细比对前行数：%d", old_rows_before_line_id_filter)
    _logger.info("旧 ERP 合并行数：%d", len(old_converted))
    _logger.info("合并后总行数：%d", len(merged))
    _logger.info("输出文件：%s", output_file)
    _logger.info("文档版输出文件：%s", document_output_file)
    _logger.info("总耗时：%.1f 秒", perf_counter() - total_start)
    _logger.info("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError, KeyError) as error:
        _logger.error("合并失败：%s", error)
        raise SystemExit(1) from error
    except Exception as error:  # noqa: BLE001 — 兜底避免崩溃无提示
        _logger.exception("发生未预期的错误：%s", error)
        raise SystemExit(2) from error
