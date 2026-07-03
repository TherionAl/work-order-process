from work_order_process.dictionary import fallback_dictionary
from work_order_process.transform import enrich_tickets, filter_tickets_since, random_sample


def test_filter_tickets_since_keeps_2025_plus() -> None:
    tickets = [
        {"ticketId": 1, "createDT": "2024-12-31 23:59:59"},
        {"ticketId": 2, "createDT": "2025-01-01 00:00:00"},
    ]

    assert [item["ticketId"] for item in filter_tickets_since(tickets, "2025-01-01")] == [2]


def test_enrich_tickets_attaches_contact_and_customer() -> None:
    dictionary = fallback_dictionary()
    tickets = [{"ticketId": 1, "custUserId": 2, "subject": "测试"}]
    contacts = [{"cId": 2, "realName": "张三", "userId": 3}]
    customers = [{"uId": 3, "companyName": "测试公司"}]

    enriched = enrich_tickets(tickets, contacts, customers, dictionary)

    assert enriched[0]["工单"]["标题"] == "测试"
    assert enriched[0]["联系人"]["姓名"] == "张三"
    assert enriched[0]["客户"]["公司名称"] == "测试公司"


def test_random_sample_is_seedable() -> None:
    rows = [{"id": idx} for idx in range(20)]

    assert random_sample(rows, 3, seed=1) == random_sample(rows, 3, seed=1)
