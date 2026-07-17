from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..config import load_settings
from ..erp_import import import_erp_xlsx
from .config import load_config
from .pipeline import build_standard_sheet, merge_erp_sources, write_standard_sheet

logger = logging.getLogger(__name__)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """解析ERP合并命令行参数"""
    parser = argparse.ArgumentParser(description="合并新旧ERP数据并计算年度分摊服务费")
    parser.add_argument("--config", type=Path, required=True, help="新旧 ERP 字段对照 Excel 路径")
    parser.add_argument("--input-new", type=Path, required=True, help="新 ERP 源 Excel 路径")
    parser.add_argument("--input-old", type=Path, required=True, help="旧 ERP 源 Excel 路径")
    parser.add_argument("--output", type=Path, required=True, help="标准 Sheet1 输出 Excel 路径")
    parser.add_argument("--last-year-start", help="去年统计起始日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--last-year-end", help="去年统计截止日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--current-year-start", help="今年统计起始日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--current-year-end", help="今年统计截止日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--import", dest="import_to_db", action="store_true", help="将生成的标准 Sheet1 导入 MySQL")
    parser.add_argument("--document-output", type=Path, help="预留的文档版 Excel 输出路径")
    return parser.parse_args(list(argv) if argv is not None else None)


def setup_logging() -> None:
    """配置日志输出格式与级别"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: Iterable[str] | None = None) -> None:
    setup_logging()
    args = parse_args(argv)
    config = load_config()
    date_range = config["统计日期区间"]
    previous_period = (
        args.last_year_start or date_range["去年起始"],
        args.last_year_end or date_range["去年截止"],
    )
    current_period = (
        args.current_year_start or date_range["今年起始"],
        args.current_year_end or date_range["今年截止"],
    )
    logger.info("ERP合并功能启动，配置加载成功")
    logger.info("统计日期区间：%s 至 %s", previous_period[0], current_period[1])

    merged = merge_erp_sources(args.input_new, args.input_old, args.config, datetime.now())
    standard = build_standard_sheet(merged, previous_period, current_period)
    write_standard_sheet(standard, args.output)
    logger.info("已生成标准 Sheet1：%s（%d 行）", args.output, len(standard))

    if args.import_to_db:
        result = import_erp_xlsx(load_settings().mysql, args.output)
        logger.info("ERP 导入完成：%s", result)


if __name__ == "__main__":
    main()
