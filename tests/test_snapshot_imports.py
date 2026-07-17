from pathlib import Path

from work_order_process.customer_account_import import (
    COLUMN_MAP as CUSTOMER_ACCOUNT_COLUMN_MAP,
    convert as convert_customer_account,
)
from work_order_process.erp_import import (
    COLUMN_MAP as ERP_COLUMN_MAP,
    apply_baseline_sales_platform,
    apply_sales_platform_system_engineer,
    convert as convert_erp,
)


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


def test_erp_import_reuses_baseline_sales_platform_for_existing_line() -> None:
    row = {
        "contract_id": "HT-001",
        "item_code": "ITEM-1",
        "exec_detail_id": "EXEC-1",
        "sales_platform": "new excel platform",
    }
    baseline = {
        ("HT-001", "ITEM-1", "EXEC-1"): "20260713 platform",
    }

    apply_baseline_sales_platform(row, baseline)

    assert row["sales_platform"] == "20260713 platform"


def test_erp_import_keeps_excel_sales_platform_for_new_line() -> None:
    row = {
        "contract_id": "HT-002",
        "item_code": "ITEM-2",
        "exec_detail_id": "EXEC-2",
        "sales_platform": "new excel platform",
    }
    baseline = {
        ("HT-001", "ITEM-1", "EXEC-1"): "20260713 platform",
    }

    apply_baseline_sales_platform(row, baseline)

    assert row["sales_platform"] == "new excel platform"


def test_erp_import_sets_system_engineer_from_fixed_sales_platform_mapping() -> None:
    row = {
        "sales_platform": "深圳分公司",
        "system_engineer": "excel engineer",
    }

    apply_sales_platform_system_engineer(row)

    assert row["system_engineer"] == "梁通"


def test_erp_import_keeps_excel_system_engineer_for_unmapped_platform() -> None:
    row = {
        "sales_platform": "未配置平台",
        "system_engineer": "excel engineer",
    }

    apply_sales_platform_system_engineer(row)

    assert row["system_engineer"] == "excel engineer"


def test_erp_import_maps_system_engineer_after_baseline_sales_platform() -> None:
    row = {
        "contract_id": "HT-001",
        "item_code": "ITEM-1",
        "exec_detail_id": "EXEC-1",
        "sales_platform": "new excel platform",
        "system_engineer": "excel engineer",
    }
    baseline = {
        ("HT-001", "ITEM-1", "EXEC-1"): "吉林分公司",
    }

    apply_baseline_sales_platform(row, baseline)
    apply_sales_platform_system_engineer(row)

    assert row["sales_platform"] == "吉林分公司"
    assert row["system_engineer"] == "梁通"
