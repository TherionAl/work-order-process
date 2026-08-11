from __future__ import annotations

import pytest
from openpyxl import Workbook

from work_order_process.ticket_writeback import (
    COMPLIANT_OPTION_VALUE,
    FAILURE_REASON_FIELD_KEY,
    NONCOMPLIANT_OPTION_VALUE,
    ReportScope,
    SamplingStatusSource,
    assert_hubei_writeback_scope,
    build_failure_reason_plan,
    build_sampling_status_plan,
    current_custom_field_value,
    load_report_scope,
)


def test_current_custom_field_value_reads_a_matching_field_key() -> None:
    detail = {
        "custom_fields": [
            {"key": "field_100", "value": "other"},
            {"key": "field_1447", "value": "4328151"},
        ]
    }

    assert current_custom_field_value(detail, "field_1447") == "4328151"


def test_build_plan_maps_report_conclusion_to_dropdown_values_and_skips_existing() -> None:
    sources = [
        SamplingStatusSource(ticket_id="1", compliant=True),
        SamplingStatusSource(ticket_id="2", compliant=False),
        SamplingStatusSource(ticket_id="3", compliant=True),
    ]

    planned = build_sampling_status_plan(sources, {"1": None, "2": "", "3": "4328151"})

    assert [(item.ticket_id, item.target_value, item.action) for item in planned] == [
        ("1", COMPLIANT_OPTION_VALUE, "update"),
        ("2", NONCOMPLIANT_OPTION_VALUE, "update"),
        ("3", COMPLIANT_OPTION_VALUE, "skip_existing"),
    ]


def test_build_failure_reason_plan_only_writes_noncompliant_rows_without_a_reason() -> None:
    sources = [
        SamplingStatusSource(ticket_id="1", compliant=True),
        SamplingStatusSource(
            ticket_id="2", compliant=False, failure_reason="解决办法缺少原因、处理过程或附件"
        ),
        SamplingStatusSource(ticket_id="3", compliant=False, failure_reason="解决办法内容过于简单"),
    ]

    planned = build_failure_reason_plan(sources, {"2": None, "3": "人工补充的原因"})

    assert FAILURE_REASON_FIELD_KEY == "field_166"
    assert [(item.ticket_id, item.target_value, item.action) for item in planned] == [
        ("2", "解决办法缺少原因、处理过程或附件", "update"),
        ("3", "解决办法内容过于简单", "skip_existing"),
    ]


def test_hubei_writeback_scope_requires_the_exact_hubei_province() -> None:
    assert_hubei_writeback_scope(
        ReportScope(report_type="hubei_compliance_check", province="湖北省")
    )

    with pytest.raises(ValueError, match="仅允许湖北省"):
        assert_hubei_writeback_scope(
            ReportScope(report_type="hubei_compliance_check", province="湖南省")
        )


def test_hubei_writeback_scope_rejects_an_unrecognized_report_type() -> None:
    with pytest.raises(ValueError, match="不支持"):
        assert_hubei_writeback_scope(ReportScope(report_type="generic", province="湖北省"))


def test_load_report_scope_reads_hubei_scope_metadata(tmp_path) -> None:
    report_path = tmp_path / "report.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "回写范围"
    worksheet.append(["键", "值"])
    worksheet.append(["report_type", "hubei_compliance_check"])
    worksheet.append(["province", "湖北省"])
    workbook.save(report_path)

    assert load_report_scope(report_path) == ReportScope(
        report_type="hubei_compliance_check", province="湖北省"
    )
