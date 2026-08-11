from __future__ import annotations

from datetime import datetime

from work_order_process.hubei_analysis import (
    build_ticket_version_predicate,
    index_custom_fields_by_ticket_version,
    mark_duplicate_results,
)


def test_custom_field_index_keeps_versions_of_one_ticket_separate() -> None:
    older = datetime(2026, 7, 1, 9, 0)
    newer = datetime(2026, 8, 1, 9, 0)

    indexed = index_custom_fields_by_ticket_version(
        [
            {
                "ticket_id": 101,
                "create_dt": older,
                "field_name": "问题描述",
                "field_key": "problem_desc",
                "field_value": "旧版本描述",
            },
            {
                "ticket_id": 101,
                "create_dt": newer,
                "field_name": "问题描述",
                "field_key": "problem_desc",
                "field_value": "新版本描述",
            },
        ]
    )

    assert indexed[(101, older)]["问题描述"] == "旧版本描述"
    assert indexed[(101, newer)]["问题描述"] == "新版本描述"


def test_ticket_version_predicate_binds_each_ticket_to_its_create_time() -> None:
    older = datetime(2026, 7, 1, 9, 0)
    newer = datetime(2026, 8, 1, 9, 0)

    predicate, params = build_ticket_version_predicate([(101, older), (101, newer)])

    assert predicate == "(ticket_id, create_dt) IN ((%s, %s), (%s, %s))"
    assert params == (101, older, 101, newer)


def test_duplicate_check_requires_matching_title() -> None:
    created_at = datetime(2026, 8, 1, 9, 0)
    results = [
        _result(1, created_at, "登录故障", "用户点击登录后，系统提示密码错误，无法进入系统"),
        _result(2, created_at, "打印故障", "用户点击登录后，系统提示密码错误，无法进入系统"),
    ]

    mark_duplicate_results(results, threshold=0.8)

    assert [result["is_duplicate"] for result in results] == [False, False]


def test_duplicate_check_skips_rows_without_customer_name() -> None:
    created_at = datetime(2026, 8, 1, 9, 0)
    results = [
        _result(
            1, created_at, "登录故障", "用户点击登录后，系统提示密码错误，无法进入系统", company=""
        ),
        _result(
            2, created_at, "登录故障", "用户点击登录后，系统提示密码错误，无法进入系统", company=""
        ),
    ]

    mark_duplicate_results(results, threshold=0.8)

    assert [result["is_duplicate"] for result in results] == [False, False]


def test_duplicate_check_marks_later_same_title_ticket() -> None:
    created_at = datetime(2026, 8, 1, 9, 0)
    results = [
        _result(1, created_at, "登录故障", "用户点击登录后，系统提示密码错误，无法进入系统"),
        _result(2, created_at, "登录故障", "用户点击登录后，系统提示密码错误，无法进入系统"),
    ]

    mark_duplicate_results(results, threshold=0.8)

    assert [result["is_duplicate"] for result in results] == [False, True]


def _result(
    ticket_id: int,
    created_at: datetime,
    subject: str,
    problem_desc: str,
    *,
    company: str = "甲公司",
) -> dict:
    return {
        "row": {
            "ticket_id": ticket_id,
            "create_dt": created_at,
            "company_name": company,
            "subject": subject,
            "problem_desc": problem_desc,
        }
    }
