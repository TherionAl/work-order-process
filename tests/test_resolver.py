"""resolver.py 单元测试。

用 mock 数据验证字段值替换逻辑，不需要真实 API。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from work_order_process.resolver import (
    TICKET_STATUS,
    TICKET_TYPE,
    PRIORITY_LEVEL,
    YES_NO,
    CREATER_TYPE,
    TicketFieldResolver,
    _replace_enum,
    _replace_unix_timestamp,
    _split_id_list,
    _first_nonempty,
    resolve_ticket_detail_values,
)


def _make_mock_client() -> MagicMock:
    """创建一个返回固定数据的 mock 客户端。"""
    client = MagicMock()
    client.fetch_contact_detail.return_value = {"name": "张三"}
    client.fetch_support_detail.return_value = {"name": "李四"}
    client.fetch_support_group_detail.return_value = {"sgName": "技术支持组"}
    client.fetch_ticket_template_detail.return_value = {"ticketTemplateName": "产品咨询模板"}
    return client


def _make_field_resolver() -> TicketFieldResolver:
    """创建一个包含已知字段的 field resolver。"""
    fields = [
        {
            "key": "field_status",
            "name": "状态",
            "custom_field_options": [
                {"key": "1", "value": "待处理"},
                {"key": "2", "value": "处理中"},
            ],
        }
    ]
    return TicketFieldResolver(fields)


def test_first_nonempty_returns_first_non_blank() -> None:
    assert _first_nonempty("", None, "hello", "world") == "hello"


def test_first_nonempty_returns_empty_when_all_blank() -> None:
    assert _first_nonempty("", None, "   ") == ""


def test_split_id_list_handles_comma_separated() -> None:
    assert _split_id_list("1,2,3") == ["1", "2", "3"]


def test_split_id_list_handles_chinese_comma() -> None:
    assert _split_id_list("1，2，3") == ["1", "2", "3"]


def test_split_id_list_handles_list_input() -> None:
    assert _split_id_list(["1", "2", "3"]) == ["1", "2", "3"]


def test_split_id_list_handles_none() -> None:
    assert _split_id_list(None) == []


def test_replace_enum_with_known_value() -> None:
    row = {"ticketType": "1"}
    _replace_enum(row, "ticketType", TICKET_TYPE)
    assert row["ticketType"] == "问题"


def test_replace_enum_with_unknown_value_preserves_original() -> None:
    row = {"ticketType": "99"}
    _replace_enum(row, "ticketType", TICKET_TYPE)
    assert row["ticketType"] == "99"


def test_replace_enum_with_empty_value_preserves_original() -> None:
    row = {"ticketType": ""}
    _replace_enum(row, "ticketType", TICKET_TYPE)
    assert row["ticketType"] == ""


def test_replace_unix_timestamp() -> None:
    """验证 Unix 时间戳被替换为可读时间（本地时区）。"""
    import datetime

    row = {"nodeFieldIntoTime": 1719849600}
    _replace_unix_timestamp(row, "nodeFieldIntoTime")
    expected = datetime.datetime.fromtimestamp(1719849600).strftime("%Y-%m-%d %H:%M:%S")
    assert row["nodeFieldIntoTime"] == expected


def test_replace_unix_timestamp_preserves_zero() -> None:
    row = {"nodeFieldIntoTime": 0}
    _replace_unix_timestamp(row, "nodeFieldIntoTime")
    assert row["nodeFieldIntoTime"] == 0


def test_replace_unix_timestamp_preserves_empty() -> None:
    row = {"nodeFieldIntoTime": ""}
    _replace_unix_timestamp(row, "nodeFieldIntoTime")
    assert row["nodeFieldIntoTime"] == ""


def test_resolve_ticket_detail_values_replaces_enums() -> None:
    """验证 resolve_ticket_detail_values 正确替换枚举值。"""
    detail = {
        "ticketId": "12345",
        "subject": "测试工单",
        "ticketType": "2",
        "priorityLevel": "3",
        "ticketStatus": "4",
        "createrType": "0",
        "isDeleted": "0",
    }
    client = _make_mock_client()
    field_resolver = _make_field_resolver()

    result = resolve_ticket_detail_values(detail, client, field_resolver)

    assert result["ticketType"] == "事务"
    assert result["priorityLevel"] == "高"
    assert result["ticketStatus"] == "已解决"
    assert result["createrType"] == "客服"
    assert result["isDeleted"] == "否"
    # 原始数据不应被修改
    assert detail["ticketType"] == "2"


def test_resolve_ticket_detail_values_adds_contact_name() -> None:
    """验证保留 custUserId 原值，同时新增 cust_user_name。"""
    detail = {"ticketId": "1", "custUserId": "100"}
    client = _make_mock_client()
    field_resolver = _make_field_resolver()

    result = resolve_ticket_detail_values(detail, client, field_resolver)

    assert result["custUserId"] == "100"              # 原 ID 保留
    assert result["cust_user_name"] == "张三"          # 新增姓名


def test_resolve_ticket_detail_values_adds_support_names() -> None:
    """验证保留 servicerUserId/createrId/deleterId 原值，同时新增 name 字段。"""
    detail = {
        "ticketId": "1",
        "servicerUserId": "200",
        "createrId": "201",
        "deleterId": "202",
    }
    client = _make_mock_client()
    field_resolver = _make_field_resolver()

    result = resolve_ticket_detail_values(detail, client, field_resolver)

    assert result["servicerUserId"] == "200"
    assert result["servicer_user_name"] == "李四"
    assert result["createrId"] == "201"
    assert result["creater_name"] == "李四"
    assert result["deleterId"] == "202"
    assert result["deleter_name"] == "李四"


def test_resolve_ticket_detail_values_adds_support_group_name() -> None:
    """验证保留 servicerGroupId 原值，同时新增 servicer_group_name。"""
    detail = {"ticketId": "1", "servicerGroupId": "300"}
    client = _make_mock_client()
    field_resolver = _make_field_resolver()

    result = resolve_ticket_detail_values(detail, client, field_resolver)

    assert result["servicerGroupId"] == "300"
    assert result["servicer_group_name"] == "技术支持组"


def test_resolve_ticket_detail_values_adds_template_name() -> None:
    """验证保留 ticketTemplateId 原值，同时新增 ticket_template_name。"""
    detail = {"ticketId": "1", "ticketTemplateId": "400"}
    client = _make_mock_client()
    field_resolver = _make_field_resolver()

    result = resolve_ticket_detail_values(detail, client, field_resolver)

    assert result["ticketTemplateId"] == "400"
    assert result["ticket_template_name"] == "产品咨询模板"


def test_resolve_ticket_detail_values_does_not_mutate_original() -> None:
    """验证原始 detail 不被修改（deepcopy 保护）。"""
    detail = {
        "ticketId": "1",
        "ticketType": "1",
        "servicerUserId": "100",
    }
    client = _make_mock_client()
    field_resolver = _make_field_resolver()

    resolve_ticket_detail_values(detail, client, field_resolver)

    assert detail["ticketType"] == "1"  # 原始未被修改
    assert detail["servicerUserId"] == "100"


def test_resolve_ticket_detail_values_places_region_province_correctly() -> None:
    detail = {
        "ticketId": "1",
        "custom_fields": [{"key": "field_region", "value": "\u6e56\u5317\u7701"}],
    }
    client = _make_mock_client()
    field_resolver = TicketFieldResolver(
        [{"key": "field_region", "name": "\u5730\u533a", "custom_field_options": []}]
    )

    result = resolve_ticket_detail_values(detail, client, field_resolver)

    assert result["province"] == "\u6e56\u5317\u7701"
    assert result["region_text"] == "\u6e56\u5317\u7701"
    assert "district" not in result


def test_resolve_ticket_detail_values_extracts_ticket_category() -> None:
    detail = {
        "ticketId": "1",
        "custom_fields": [{"key": "field_category", "value": "\u5b50\u5355"}],
    }
    client = _make_mock_client()
    field_resolver = TicketFieldResolver(
        [{"key": "field_category", "name": "\u5de5\u5355\u7c7b\u522b", "custom_field_options": []}]
    )

    result = resolve_ticket_detail_values(detail, client, field_resolver)

    assert result["ticket_category"] == "\u5b50\u5355"


def test_field_resolver_option_value_handles_list() -> None:
    """验证 option_value 能处理列表输入。"""
    resolver = _make_field_resolver()
    result = resolver.option_value(["1", "2"], "field_status")
    assert result == ["待处理", "处理中"]


def test_field_resolver_option_value_handles_dict() -> None:
    """验证 option_value 能处理字典输入。"""
    resolver = _make_field_resolver()
    result = resolver.option_value({"status": "1"}, "field_status")
    assert result == {"status": "待处理"}


def test_enum_mappings_are_complete() -> None:
    """验证所有枚举映射不包含空值。"""
    assert TICKET_TYPE["1"] == "问题"
    assert TICKET_TYPE["2"] == "事务"
    assert TICKET_TYPE["3"] == "故障"
    assert TICKET_TYPE["4"] == "任务"

    assert PRIORITY_LEVEL["1"] == "低"
    assert PRIORITY_LEVEL["4"] == "紧急"

    assert TICKET_STATUS["1"] == "新建"
    assert TICKET_STATUS["5"] == "已关闭"

    assert CREATER_TYPE["0"] == "客服"
    assert CREATER_TYPE["1"] == "客户"

    assert YES_NO["0"] == "否"
    assert YES_NO["1"] == "是"
