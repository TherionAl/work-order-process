from work_order_process.erp_import import COLUMN_MAP
from work_order_process.erp_schema import (
    ALLOCATION_COLUMN_MAP,
    LEGACY_ERP_COLUMN_MAP,
    STANDARD_ERP_COLUMN_MAP,
    legacy_headers,
    standard_headers,
)


def test_standard_erp_schema_appends_allocation_columns_to_legacy_contract() -> None:
    assert len(LEGACY_ERP_COLUMN_MAP) == 69
    assert len(STANDARD_ERP_COLUMN_MAP) == 78
    assert STANDARD_ERP_COLUMN_MAP[:69] == LEGACY_ERP_COLUMN_MAP
    assert STANDARD_ERP_COLUMN_MAP[69:] == ALLOCATION_COLUMN_MAP


def test_standard_erp_schema_preserves_amortization_meanings() -> None:
    standard = dict(STANDARD_ERP_COLUMN_MAP)

    assert standard["当年应分摊金额"] == "cur_year_amort"
    assert standard["去年同期应分摊金额"] == "prev_year_amort"
    assert standard["去年按期分摊服务费"] == "prev_year_calc_amort"
    assert standard["今年按期分摊服务费"] == "cur_year_calc_amort"


def test_erp_schema_headers_and_import_legacy_alias_match() -> None:
    assert legacy_headers() == [header for header, _ in LEGACY_ERP_COLUMN_MAP]
    assert standard_headers() == [header for header, _ in STANDARD_ERP_COLUMN_MAP]
    assert COLUMN_MAP is LEGACY_ERP_COLUMN_MAP or COLUMN_MAP == LEGACY_ERP_COLUMN_MAP
