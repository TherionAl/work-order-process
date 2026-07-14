"""客户台账明细汇总表 Excel → MySQL 导入。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from .config import MySQLConfig
from .auxiliary_schema import ensure_auxiliary_schema

logger = logging.getLogger(__name__)

# Excel 列名 → DB 列名（按顺序对应 Sheet1 的 40 列）
COLUMN_MAP = [
    ("营销平台", "marketing_platform"),
    ("合同签约客户", "contract_sign_customer"),
    ("最终使用客户", "final_user_customer"),
    ("应收年运维费", "annual_ops_fee"),
    ("业务分类", "business_category"),
    ("是否纳入当年运维收费目标", "is_in_target"),
    ("服务到期时间", "service_expire_date"),
    ("签约进度", "sign_progress"),
    ("合同编码", "contract_code"),
    ("标的行编码", "item_code"),
    ("省份", "province"),
    ("城市", "city"),
    ("区县", "district"),
    ("运维收费项", "ops_item"),
    ("环境项目名称", "env_project_name"),
    ("客户类型", "customer_type"),
    ("销售部门", "sales_dept"),
    ("销售人员", "sales_person"),
    ("当年未签分类", "unsigned_category"),
    ("不纳入原因分类", "exclude_reason_category"),
    ("不纳入原因说明", "exclude_reason_desc"),
    ("合同名称", "contract_name"),
    ("合同申请日期", "contract_apply_date"),
    ("合同类型", "contract_type"),
    ("归档状态", "archive_status"),
    ("是否虚拟合同", "is_virtual"),
    ("运维开始日期", "ops_start_date"),
    ("运维结束日期", "ops_end_date"),
    ("明细合同金额", "detail_amount"),
    ("预计确收金额", "expected_revenue"),
    ("预计回款金额", "expected_collection"),
    ("已确收金额", "actual_revenue"),
    ("已回款金额", "actual_collection"),
    ("实际验收日期", "acceptance_date"),
    ("合同次数", "contract_count"),
    ("付款方式", "payment_method"),
    ("单位联系人", "contact_person"),
    ("单位联系方式", "contact_phone"),
    ("客户沟通情况", "communication_detail"),
    ("备注", "remark"),
]

INSERT_SQL = (
    "INSERT INTO customer_account ("
    + ", ".join(col for _, col in COLUMN_MAP)
    + ", create_date) VALUES ("
    + ", ".join(["%s"] * (len(COLUMN_MAP) + 1))
    + ")"
)


def _to_date(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
    return s


def _to_decimal(value) -> float | None:
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


CONVERTERS = {
    "annual_ops_fee": _to_decimal,
    "service_expire_date": _to_date,
    "contract_apply_date": _to_date,
    "ops_start_date": _to_date,
    "ops_end_date": _to_date,
    "detail_amount": _to_decimal,
    "expected_revenue": _to_decimal,
    "expected_collection": _to_decimal,
    "actual_revenue": _to_decimal,
    "actual_collection": _to_decimal,
    "acceptance_date": _to_date,
    "contract_count": _to_int,
}


def convert(col_name: str, value) -> object:
    fn = CONVERTERS.get(col_name)
    if fn:
        return fn(value)
    return _to_str(value)


def import_customer_account_xlsx(
    config: MySQLConfig,
    file_path: Path,
    create_date: str,
    sheet_name: str | None = None,
    batch_size: int = 5000,
) -> dict:
    """把客户台账明细 Excel 导入 MySQL。

    返回 {"file": ..., "rows": ..., "inserted": ..., "skipped": ..., "seconds": ...}
    """
    import pymysql
    import time

    ensure_auxiliary_schema(config)
    logger.info("打开文件: %s", file_path)
    wb = load_workbook(file_path, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]

    headers: list[str] = []
    inserted = 0
    skipped = 0
    cleaned = 0
    started = time.time()

    conn = pymysql.connect(
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
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c).strip() if c else "" for c in row]
                    if headers != [cn for cn, _ in COLUMN_MAP]:
                        logger.warning("Excel 列头不完全匹配定义，仍按列顺序映射")
                    continue

                db_values = []
                for j, (_, col) in enumerate(COLUMN_MAP):
                    val = row[j] if j < len(row) else None
                    db_values.append(convert(col, val))

                # 数据清洗：合同签约客户、最终使用客户都为空则跳过
                if db_values[1] is None and db_values[2] is None:
                    cleaned += 1
                    continue

                db_values.append(create_date)

                try:
                    cursor.execute(INSERT_SQL, db_values)
                    if cursor.rowcount:
                        inserted += 1
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
        "导入完成: 插入 %d, 跳过 %d, 清洗 %d, 耗时 %ss",
        inserted, skipped, cleaned, seconds,
    )
    return {
        "file": file_path.name,
        "rows": i - 1 if i else 0,
        "inserted": inserted,
        "skipped": skipped,
        "cleaned": cleaned,
        "seconds": seconds,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="客户台账明细数据入库")
    parser.add_argument("--file", required=True, help="Excel 文件路径")
    parser.add_argument("--create-date", required=True, help="数据日期，如 20260710")
    parser.add_argument("--sheet", default=None, help="Sheet 名称（默认第一个）")
    parser.add_argument("--batch-size", type=int, default=5000, help="每批提交行数")
    args = parser.parse_args()

    from .config import load_settings
    settings = load_settings()
    result = import_customer_account_xlsx(
        settings.mysql, Path(args.file), args.create_date, args.sheet, args.batch_size
    )
    print(result)


if __name__ == "__main__":
    main()
