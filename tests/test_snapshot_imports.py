from pathlib import Path

from work_order_process.customer_account_import import (
    COLUMN_MAP as CUSTOMER_ACCOUNT_COLUMN_MAP,
    convert as convert_customer_account,
)
from work_order_process.erp_import import COLUMN_MAP as ERP_COLUMN_MAP, convert as convert_erp


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_erp_import_uses_create_date_snapshot_key() -> None:
    assert dict(ERP_COLUMN_MAP)["文件生成时间戳"] == "create_date"
    assert convert_erp("create_date", 20260713165231) == "20260713"
    assert "create_date" in (PROJECT_ROOT / "sql" / "erp_data.sql").read_text(encoding="utf-8")


def test_customer_account_mapping_matches_server_snapshot_columns() -> None:
    columns = [column for _, column in CUSTOMER_ACCOUNT_COLUMN_MAP]
    ddl = (PROJECT_ROOT / "sql" / "customer_account.sql").read_text(encoding="utf-8")

    assert len(columns) == 40
    assert "contract_sign_customer" in columns
    assert "final_user_customer" in columns
    assert "create_date VARCHAR(8) NOT NULL" in ddl
    assert "PRIMARY KEY (id, create_date)" in ddl
    assert convert_customer_account("annual_ops_fee", "1,234.50") == 1234.5
