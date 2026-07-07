from work_order_process.dictionary import fallback_dictionary
from work_order_process.structured_ticket import (
    build_custom_field_excel_rows,
    build_main_excel_rows,
    build_ticket_detail_custom_field_rows,
    build_ticket_detail_main_row,
)


def test_build_ticket_detail_main_row_normalizes_values() -> None:
    detail = {
        "ticketId": "22256891",
        "custUserId": "王涛",
        "subject": "测试工单",
        "createDT": "2025-01-02 03:04:05",
        "descriptattachments": [{"name": "a.txt"}],
    }

    row = build_ticket_detail_main_row(detail)

    assert row["ticket_id"] == 22256891
    assert row["cust_user_id"] == "王涛"
    assert row["subject"] == "测试工单"
    assert row["create_dt"].year == 2025
    assert row["descript_attachments"] == '[{"name": "a.txt"}]'


def test_build_ticket_detail_custom_field_rows_preserves_key_and_resolved_name() -> None:
    raw_detail = {
        "ticketId": "22256891",
        "ticketTemplateId": "104",
        "custom_fields": [{"key": "field_1", "value": "39911948"}],
    }
    value_detail = {
        "ticketId": "22256891",
        "ticketTemplateId": "服务器监控模板",
        "custom_fields": [{"key": "客户性质", "value": "正式客户"}],
    }

    rows = build_ticket_detail_custom_field_rows(raw_detail, value_detail)

    assert rows == [
        {
            "ticket_id": 22256891,
            "ticket_template_id": "服务器监控模板",
            "field_order": 1,
            "field_key": "field_1",
            "field_name": "客户性质",
            "field_value": "正式客户",
            "field_value_json": None,
            "field_value_type": "str",
        }
    ]


def test_excel_rows_use_shared_structured_mapping() -> None:
    dictionary = fallback_dictionary()
    raw_detail = {
        "ticketId": "1",
        "subject": "标题",
        "custom_fields": [{"key": "field_1", "value": "1"}],
    }
    value_detail = {
        "ticketId": "1",
        "subject": "标题",
        "custom_fields": [{"key": "字段一", "value": "选项一"}],
    }

    main_rows = build_main_excel_rows(raw_detail, value_detail, dictionary)
    custom_rows = build_custom_field_excel_rows(raw_detail, value_detail)

    assert main_rows[0] == ["工单ID", "英文字段", "中文字段", "值", "值类型"]
    assert custom_rows[1] == ["1", "", 1, "field_1", "字段一", "选项一", "str"]
