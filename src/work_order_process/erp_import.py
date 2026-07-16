"""ERP 新旧合并数据 Excel → MySQL 导入。"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .config import MySQLConfig
from .auxiliary_schema import ensure_auxiliary_schema
from .erp_schema import LEGACY_ERP_COLUMN_MAP

logger = logging.getLogger(__name__)

BASELINE_SALES_PLATFORM_CREATE_DATE = "20260713"
SALES_PLATFORM_BASELINE_KEY_COLUMNS = ("contract_id", "item_code", "exec_detail_id")
SYSTEM_ENGINEER_BY_SALES_PLATFORM = {
    "博思智合": "黄迪",
    "广东瑞联": "黄迪",
    "广西分公司": "黄迪",
    "贵州分公司": "黄迪",
    "河北分公司": "黄迪",
    "深圳分公司": "梁通",
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
    "吉林分公司": "梁通",
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

# Excel 列名 → DB 列名（按顺序对应 Sheet1 的 66 列）
COLUMN_MAP = LEGACY_ERP_COLUMN_MAP

INSERT_SQL = (
    "INSERT INTO erp_data ("
    + ", ".join(col for _, col in COLUMN_MAP)
    + ") VALUES ("
    + ", ".join(["%s"] * len(COLUMN_MAP))
    + ") ON DUPLICATE KEY UPDATE "
    + ", ".join(
        f"{col} = VALUES({col})"
        for _, col in COLUMN_MAP
        if col not in {"contract_id", "item_code", "exec_detail_id", "create_date"}
    )
)


def _to_date(value) -> str | None:
    """把 Excel 日期值转为 YYYY-MM-DD 字符串。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
    return s


def _to_decimal(value) -> float | None:
    """把数值转为 Decimal。"""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _to_str(value) -> str | None:
    if value is None or value == "":
        return None
    s = str(value).strip()
    return s if s else None


def _to_create_date(value) -> str | None:
    """把 Excel file_timestamp 20260713165231 转为 20260713。"""
    if value is None or value == "":
        return None
    s = str(value).strip()
    return s[:8] if len(s) >= 8 else s


# 值转换函数
CONVERTERS = {
    "seq_no": _to_int,
    "create_date": _to_create_date,
    "contract_apply_date": _to_date,
    "archive_date": _to_date,
    "ops_start_date": _to_date,
    "ops_end_date": _to_date,
    "total_amount": _to_decimal,
    "free_ops_months": _to_int,
    "annual_ops_amount": _to_decimal,
    "detail_qty": _to_int,
    "unit_price": _to_decimal,
    "detail_amount_with_tax": _to_decimal,
    "product_amount": _to_decimal,
    "cum_billing": _to_decimal,
    "cum_collection": _to_decimal,
    "cum_revenue": _to_decimal,
    "cur_year_billing": _to_decimal,
    "prev_year_billing": _to_decimal,
    "cur_year_collection": _to_decimal,
    "prev_year_collection": _to_decimal,
    "cur_year_revenue": _to_decimal,
    "prev_year_revenue": _to_decimal,
    "cur_year_amort": _to_decimal,
    "prev_year_amort": _to_decimal,
}


def convert(col_name: str, value) -> object:
    fn = CONVERTERS.get(col_name)
    if fn:
        return fn(value)
    return _to_str(value)


SalesPlatformBaseline = dict[tuple[str, str | None, str | None], str | None]
SystemEngineerMapping = dict[str, str]


def _baseline_key(row: dict[str, Any]) -> tuple[str, str | None, str | None] | None:
    contract_id = row.get("contract_id")
    if contract_id is None:
        return None
    return (
        str(contract_id),
        row.get("item_code"),
        row.get("exec_detail_id"),
    )


def apply_baseline_sales_platform(
    row: dict[str, Any],
    baseline: SalesPlatformBaseline,
    baseline_create_date: str = BASELINE_SALES_PLATFORM_CREATE_DATE,
) -> bool:
    """Reuse the 20260713 sales_platform for business lines that already existed.

    Only sales_platform is special-cased. All other values in row remain the
    current Excel import values.
    """
    if row.get("create_date") == baseline_create_date:
        return False
    key = _baseline_key(row)
    if key is None or key not in baseline:
        return False
    row["sales_platform"] = baseline[key]
    return True


def load_sales_platform_baseline(cursor: Any, create_date: str = BASELINE_SALES_PLATFORM_CREATE_DATE) -> SalesPlatformBaseline:
    """Load the sales_platform baseline keyed by contract/item/exec detail."""
    cursor.execute(
        """
        SELECT contract_id, item_code, exec_detail_id, sales_platform
        FROM erp_data
        WHERE create_date = %s
        """,
        (create_date,),
    )
    baseline: SalesPlatformBaseline = {}
    for contract_id, item_code, exec_detail_id, sales_platform in cursor.fetchall():
        baseline[(str(contract_id), item_code, exec_detail_id)] = sales_platform
    return baseline


def apply_sales_platform_system_engineer(
    row: dict[str, Any],
    mapping: SystemEngineerMapping = SYSTEM_ENGINEER_BY_SALES_PLATFORM,
) -> bool:
    """Set system_engineer from the final sales_platform fixed mapping."""
    sales_platform = row.get("sales_platform")
    if not sales_platform or sales_platform not in mapping:
        return False
    row["system_engineer"] = mapping[sales_platform]
    return True


def import_erp_xlsx(config: MySQLConfig, file_path: Path, batch_size: int = 5000) -> dict:
    """把 ERP Excel 导入 MySQL。

    返回 {"file": ..., "rows": ..., "inserted": ..., "skipped": ..., "seconds": ...}
    """
    import pymysql
    import time

    ensure_auxiliary_schema(config)
    file_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    logger.info("打开文件: %s", file_path)
    wb = load_workbook(file_path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    headers: list[str] = []
    inserted = 0
    updated = 0
    unchanged = 0
    skipped = 0
    reused_baseline_sales_platform = 0
    new_sales_platform = 0
    applied_system_engineer_mapping = 0
    kept_excel_system_engineer = 0
    started = time.time()

    pymysql_mod = pymysql
    conn = pymysql_mod.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
    )

    try:
        with conn.cursor() as cursor:
            sales_platform_baseline = load_sales_platform_baseline(cursor)
            logger.info(
                "已加载 %s 行 %s 营销平台基准",
                len(sales_platform_baseline),
                BASELINE_SALES_PLATFORM_CREATE_DATE,
            )
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c).strip() if c else "" for c in row]
                    if headers != [cn for cn, _ in COLUMN_MAP]:
                        logger.warning("Excel 列头不完全匹配定义，仍按列顺序映射")
                    continue

                db_row: dict[str, Any] = {}
                for j, (_, col) in enumerate(COLUMN_MAP):
                    val = row[j] if j < len(row) else None
                    db_row[col] = convert(col, val)

                # 合同编号是业务必填字段。
                if db_row["contract_id"] is None:
                    skipped += 1
                    continue

                if apply_baseline_sales_platform(db_row, sales_platform_baseline):
                    reused_baseline_sales_platform += 1
                else:
                    new_sales_platform += 1

                if apply_sales_platform_system_engineer(db_row):
                    applied_system_engineer_mapping += 1
                else:
                    kept_excel_system_engineer += 1

                db_values = [db_row[col] for _, col in COLUMN_MAP]

                try:
                    cursor.execute(INSERT_SQL, db_values)
                    if cursor.rowcount == 1:
                        inserted += 1
                    elif cursor.rowcount == 2:
                        updated += 1
                    elif cursor.rowcount == 0:
                        unchanged += 1
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1

                if (i % batch_size) == 0:
                    conn.commit()
                    logger.info("已处理 %d 行 ...", i)

        conn.commit()
    finally:
        conn.close()
        wb.close()

    seconds = round(time.time() - started, 1)
    logger.info(
        "导入完成: 插入 %d, 更新 %d, 未变化 %d, 跳过 %d, 复用基准营销平台 %d, 套用体系工程师映射 %d, 耗时 %ss",
        inserted,
        updated,
        unchanged,
        skipped,
        reused_baseline_sales_platform,
        applied_system_engineer_mapping,
        seconds,
    )
    return {
        "file": file_path.name,
        "rows": i - 1 if i else 0,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "reused_baseline_sales_platform": reused_baseline_sales_platform,
        "new_sales_platform": new_sales_platform,
        "applied_system_engineer_mapping": applied_system_engineer_mapping,
        "kept_excel_system_engineer": kept_excel_system_engineer,
        "seconds": seconds,
    }


def main():
    parser = argparse.ArgumentParser(description="ERP Excel 数据入库")
    parser.add_argument("--file", required=True, help="ERP Excel 文件路径")
    parser.add_argument("--batch-size", type=int, default=5000, help="每批提交行数")
    args = parser.parse_args()

    from .config import load_settings
    settings = load_settings()
    result = import_erp_xlsx(settings.mysql, Path(args.file), args.batch_size)
    print(result)


if __name__ == "__main__":
    main()
