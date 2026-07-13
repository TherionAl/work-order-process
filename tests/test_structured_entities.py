from work_order_process.structured_entities import (
    CONTACT_HASH_FIELDS,
    CUSTOMER_HASH_FIELDS,
    build_contact_row,
    build_customer_row,
    entity_row_hash,
)


def test_build_customer_row_normalizes_customer_and_company_fields() -> None:
    record = {
        "uId": 39911948,
        "companyName": "测试公司",
        "rank": "正式客户",
        "area": "福建省",
        "area2": "福州市",
        "address": "软件园",
        "contactor": "王涛",
        "mobile": "13800000000",
        "email": "test@example.com",
        "updateTime": "2025-01-02 03:04:05",
    }

    row = build_customer_row(record, "customer")

    assert row["customer_id"] == "39911948"
    assert row["customer_name"] == "测试公司"
    assert row["customer_type"] == "正式客户"
    assert row["province"] == "福建省"
    assert row["city"] == "福州市"
    assert row["contact_name"] == "王涛"
    assert row["phone"] == "13800000000"
    assert row["source_flags"] == "customer"
    assert row["source_updated_at"].year == 2025


def test_build_contact_row_normalizes_contact_and_company_contact_fields() -> None:
    record = {
        "cId": "40132378",
        "realName": "王涛",
        "mobile": "13800000000",
        "fixnumber": "0591-123456",
        "QQ": "123456",
        "userId": "39911948",
        "companyName": "测试公司",
        "position": "财务",
        "updateTime": "2025-01-02",
    }

    row = build_contact_row(record, "contact")

    assert row["contact_id"] == "40132378"
    assert row["contact_name"] == "王涛"
    assert row["phone"] == "13800000000"
    assert row["fixed_phone"] == "0591-123456"
    assert row["qq"] == "123456"
    assert row["customer_id"] == "39911948"
    assert row["customer_name"] == "测试公司"
    assert row["position_name"] == "财务"
    assert row["source_flags"] == "contact"
    assert row["source_updated_at"].year == 2025


def test_customer_hash_is_stable_and_ignores_sync_metadata() -> None:
    row = build_customer_row({"uId": "C1", "companyName": "Example"}, "companies")
    with_sync_metadata = {**row, "last_sync_at": "later", "sync_batch_id": "batch-2"}

    assert entity_row_hash(row, CUSTOMER_HASH_FIELDS) == entity_row_hash(with_sync_metadata, CUSTOMER_HASH_FIELDS)


def test_contact_hash_changes_when_business_contact_field_changes() -> None:
    row = build_contact_row({"cId": "U1", "realName": "Alice", "mobile": "13800000000"}, "users")

    assert entity_row_hash(row, CONTACT_HASH_FIELDS) != entity_row_hash({**row, "phone": "13900000000"}, CONTACT_HASH_FIELDS)
