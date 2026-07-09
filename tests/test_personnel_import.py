import pytest

from work_order_process.personnel_import import (
    PERSONNEL_DDL,
    build_personnel_row,
)


def test_build_personnel_row_normalizes_excel_numeric_employee_no() -> None:
    row = build_personnel_row(
        {
            "人员姓名": "郭伟",
            "工号": 1000012.0,
            "所属省份": "YTH",
            "角色": "基础人员角色",
            "所属组": "一线临时分组",
        }
    )

    assert row == {
        "employee_no": "1000012",
        "person_name": "郭伟",
        "province": "YTH",
        "role_names": "基础人员角色",
        "group_name": "一线临时分组",
    }


def test_build_personnel_row_rejects_missing_employee_no() -> None:
    with pytest.raises(ValueError, match="employee_no"):
        build_personnel_row(
            {
                "人员姓名": "无工号",
                "工号": "",
                "所属省份": "YTH",
                "角色": "基础人员角色",
                "所属组": "一线临时分组",
            }
        )


def test_personnel_ddl_uses_employee_no_primary_key() -> None:
    assert "CREATE TABLE IF NOT EXISTS personnel" in PERSONNEL_DDL
    assert "employee_no VARCHAR(64) NOT NULL" in PERSONNEL_DDL
    assert "PRIMARY KEY (employee_no)" in PERSONNEL_DDL
