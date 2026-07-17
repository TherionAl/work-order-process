from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..config import load_settings
from ..erp_document_export import export_erp_snapshot_document
from ..erp_import import import_erp_dataframe
from .config import load_config
from .pipeline import (
    build_standard_sheet,
    merge_erp_sources,
    write_standard_sheet,
)

logger = logging.getLogger(__name__)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """解析ERP合并命令行参数"""
    parser = argparse.ArgumentParser(description="合并新旧ERP数据并计算年度分摊服务费")
    parser.add_argument("--config", type=Path, required=True, help="新旧 ERP 字段对照 Excel 路径")
    parser.add_argument("--input-new", type=Path, required=True, help="新 ERP 源 Excel 路径")
    parser.add_argument("--input-old", type=Path, required=True, help="旧 ERP 源 Excel 路径")
    parser.add_argument("--standard-output", type=Path, help="可选的标准 Sheet1 核对文件路径")
    parser.add_argument("--last-year-start", help="去年统计起始日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--last-year-end", help="去年统计截止日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--current-year-start", help="今年统计起始日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--current-year-end", help="今年统计截止日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--document-output", type=Path, required=True, help="数据库快照文档版 Excel 输出路径（不可导入）")
    return parser.parse_args(list(argv) if argv is not None else None)


def setup_logging() -> None:
    """配置日志输出格式与级别"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )


def validate_output_paths(standard_output: Path | None, document_output: Path) -> None:
    if standard_output is None:
        return
    resolved_output = os.path.normcase(str(standard_output.resolve()))
    resolved_document_output = os.path.normcase(str(document_output.resolve()))
    if resolved_output == resolved_document_output:
        raise ValueError(
            "--document-output and --standard-output must resolve to different paths"
        )


def main(argv: Iterable[str] | None = None) -> None:
    setup_logging()
    args = parse_args(argv)
    validate_output_paths(args.standard_output, args.document_output)
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
    if args.standard_output:
        write_standard_sheet(standard, args.standard_output)
        logger.info("已生成标准 Sheet1 核对文件：%s（%d 行）", args.standard_output, len(standard))

    mysql_config = load_settings().mysql
    result = import_erp_dataframe(mysql_config, standard)
    create_dates = result["create_dates"]
    if len(create_dates) != 1:
        raise ValueError(f"本次 ERP 入库未产生唯一快照日期：{create_dates}")
    logger.info("ERP 导入完成：%s", result)

    document_result = export_erp_snapshot_document(
        mysql_config, create_dates[0], args.document_output
    )
    logger.info("已从数据库快照导出 ERP 文档版：%s", document_result)


if __name__ == "__main__":
    main()
