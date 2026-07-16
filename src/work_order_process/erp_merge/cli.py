from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable

from .config import load_config

logger = logging.getLogger(__name__)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """解析ERP合并命令行参数"""
    parser = argparse.ArgumentParser(description="合并新旧ERP数据并计算年度分摊服务费")
    parser.add_argument("--config", type=Path, help="自定义规则配置文件路径")
    parser.add_argument("--input-new", type=Path, help="新ERP源Excel路径")
    parser.add_argument("--input-old", type=Path, help="旧ERP源Excel路径")
    parser.add_argument("--output-dir", type=Path, default=Path("output/erp_merge"), help="结果输出目录，默认为output/erp_merge")
    parser.add_argument("--last-year-start", help="去年统计起始日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--last-year-end", help="去年统计截止日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--current-year-start", help="今年统计起始日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--current-year-end", help="今年统计截止日期，支持YYYY-MM-DD或YYYYMMDD格式")
    parser.add_argument("--no-output", action="store_true", help="不输出结果Excel文件，仅打印处理统计")
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
    config = load_config(args.config)
    logger.info("ERP合并功能启动，配置加载成功")
    logger.info(f"统计日期区间：{config['统计日期区间']['去年起始']} 至 {config['统计日期区间']['今年截止']}")
    # TODO: 后续连接具体合并实现


if __name__ == "__main__":
    main()
