"""ERP 新旧合并数据 Excel → MySQL 导入。"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from .config import MySQLConfig

logger = logging.getLogger(__name__)

# Excel 列名 → DB 列名（按顺序对应 Sheet1 的 66 列）
COLUMN_MAP = [
    ("序号", "seq_no"),
    ("合同编号", "contract_id"),
    ("销售组织", "sales_org"),
    ("是否初始化", "is_initialized"),
    ("合同名称", "contract_name"),
    ("合同申请日期", "contract_apply_date"),
    ("销售业绩部门", "sales_dept"),
    ("申请人", "applicant"),
    ("销售员", "sales_person"),
    ("签约客户", "sign_customer"),
    ("最终客户", "final_customer"),
    ("第三方", "third_party"),
    ("合同类型", "contract_type"),
    ("暂估运维运营", "is_estimated_ops"),
    ("虚拟合同", "is_virtual"),
    ("2026运维saas续签合同", "is_2026_saas_renew"),
    ("单据状态", "doc_status"),
    ("关闭状态", "close_status"),
    ("合同执行状态", "exec_status"),
    ("归档状态", "archive_status"),
    ("归档日期", "archive_date"),
    ("合同总金额", "total_amount"),
    ("免费运维期（月）", "free_ops_months"),
    ("年运维约定金额", "annual_ops_amount"),
    ("城市", "city"),
    ("省份", "province"),
    ("企业版销售合同明细id", "sales_contract_detail_id"),
    ("标的行编码", "item_code"),
    ("标的", "item_name"),
    ("业务类型", "business_type"),
    ("交付项目编码", "project_code"),
    ("交付项目", "project_name"),
    ("运维签约类型", "ops_sign_type"),
    ("明细数量", "detail_qty"),
    ("销售单价", "unit_price"),
    ("明细价税合计", "detail_amount_with_tax"),
    ("明细运维开始开始日期", "ops_start_date"),
    ("明细运维结束日期", "ops_end_date"),
    ("执行明细id", "exec_detail_id"),
    ("产品物料", "product_material"),
    ("产品占比", "product_ratio"),
    ("云服务类型", "cloud_service_type"),
    ("产品金额", "product_amount"),
    ("一级产品线", "product_line1"),
    ("二级产品线", "product_line2"),
    ("产品公司", "product_company"),
    ("所属事业部", "division"),
    ("累计开票金额", "cum_billing"),
    ("累计回款金额", "cum_collection"),
    ("累计确收金额", "cum_revenue"),
    ("当年开票金额", "cur_year_billing"),
    ("去年同期开票金额", "prev_year_billing"),
    ("当年回款金额", "cur_year_collection"),
    ("去年同期回款金额", "prev_year_collection"),
    ("当年收入金额", "cur_year_revenue"),
    ("去年同期收入金额", "prev_year_revenue"),
    ("当年应分摊金额", "cur_year_amort"),
    ("去年同期应分摊金额", "prev_year_amort"),
    ("营销平台", "sales_platform"),
    ("体系工程师", "system_engineer"),
    ("是否公有云", "is_public_cloud"),
    ("是否一次性收入", "is_one_time_revenue"),
    ("合同分类", "contract_category"),
    ("业务类别", "business_category"),
    ("其他业务类型", "other_business_type"),
    ("无效合同类型", "invalid_contract_type"),
    ("数据来源", "data_source"),
    ("文件生成时间戳", "file_timestamp"),
    ("文件来源时间戳", "file_source_date"),
]

INSERT_SQL = (
    "INSERT IGNORE INTO erp_data ("
    + ", ".join(col for _, col in COLUMN_MAP)
    + ") VALUES ("
    + ", ".join(["%s"] * len(COLUMN_MAP))
    + ")"
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
        return float(value)
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


# 值转换函数
CONVERTERS = {
    "seq_no": _to_int,
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


def import_erp_xlsx(config: MySQLConfig, file_path: Path, batch_size: int = 5000) -> dict:
    """把 ERP Excel 导入 MySQL。

    返回 {"file": ..., "rows": ..., "inserted": ..., "skipped": ..., "seconds": ...}
    """
    import pymysql
    import time

    file_ts = datetime.now().strftime("%Y%m%d%H%M%S")
    logger.info("打开文件: %s", file_path)
    wb = load_workbook(file_path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    headers: list[str] = []
    inserted = 0
    skipped = 0
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

                # 第 5 列是 contract_id（必填）
                if db_values[1] is None:
                    skipped += 1
                    continue

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
    logger.info("导入完成: 插入 %d, 跳过 %d, 耗时 %ss", inserted, skipped, seconds)
    return {
        "file": file_path.name,
        "rows": i - 1 if i else 0,
        "inserted": inserted,
        "skipped": skipped,
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
