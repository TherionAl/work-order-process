from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd


def load_module(script_path: Path):
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"无法加载脚本：{script_path}")
    spec.loader.exec_module(module)
    return module


def test_old_erp_shared_amounts(module) -> None:
    output_columns = ["去年同期收入金额", "累计开票金额", "累计回款金额"]
    mapping = pd.Series(
        {
            "去年同期收入金额": "累计收入金额-去年同期",
            "累计开票金额": "累计开票金额",
            "累计回款金额": "累计回款金额",
        }
    )
    old_df = pd.DataFrame(
        {
            "累计收入金额-去年同期": ["1000", "1,000"],
            "累计开票金额": ["2000", "2,000"],
            "累计回款金额": ["3000", "3,000"],
            "分成比例": ["0.25", "25%"],
        }
    )

    converted = module.convert_old_to_new_columns(old_df, output_columns, mapping)
    expected = pd.DataFrame(
        {
            "去年同期收入金额": [250.0, 250.0],
            "累计开票金额": [500.0, 500.0],
            "累计回款金额": [750.0, 750.0],
        }
    )
    pd.testing.assert_frame_equal(
        converted[output_columns].reset_index(drop=True).astype(float),
        expected,
    )


def test_statistical_allocation_columns(module) -> None:
    df = pd.DataFrame(
        {
            "合同申请年份": [2025, 2026],
            "明细运维开始开始日期": ["20250701", "20250701"],
            "明细运维结束日期": ["20260630", "20260630"],
            "产品金额": [3650.0, 3650.0],
            "数据来源": ["新ERP", "新ERP"],
        }
    )

    result = module.add_statistical_allocation_columns(
        df,
        last_year_start="2025-01-01",
        last_year_end="2025-12-31",
        current_year_start="2026-01-01",
        current_year_end="2026-12-31",
    )

    expected_columns = [
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
    assert expected_columns == [
        column for column in result.columns if column in expected_columns
    ]
    assert result.loc[0, "合同天数"] == 365
    assert result.loc[0, "去年统计起始日期"] == pd.Timestamp("2025-01-01")
    assert result.loc[0, "去年统计截止日期"] == pd.Timestamp("2025-12-31")
    assert result.loc[0, "今年统计起始日期"] == pd.Timestamp("2026-01-01")
    assert result.loc[0, "今年统计截止日期"] == pd.Timestamp("2026-12-31")

    pd.testing.assert_series_equal(
        result["去年按期分摊服务费"].round(2),
        pd.Series([1840.0, 1840.0]),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        result["去年按期分摊服务费（去掉今年倒签的）"].round(2),
        pd.Series([1840.0, 0.0]),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        result["今年按期分摊服务费"].round(2),
        pd.Series([1810.0, 1810.0]),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        result["今年按期分摊服务费（加上倒签去年的服务费）"].round(2),
        pd.Series([1810.0, 3650.0]),
        check_names=False,
    )


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("merge_erp_data_20260715.py")
    loaded_module = load_module(target.resolve())
    test_old_erp_shared_amounts(loaded_module)
    test_statistical_allocation_columns(loaded_module)
