from work_order_process.mysql_storage import build_ticket_detail_main_row


def test_build_ticket_detail_main_row_defaults_ticket_category() -> None:
    row = build_ticket_detail_main_row(
        {
            "ticketId": "1",
            "createDT": "2026-01-01 00:00:00",
        }
    )

    assert row["ticket_category"] == "\u539f\u5355"


def test_build_ticket_detail_main_row_uses_resolved_ticket_category() -> None:
    row = build_ticket_detail_main_row(
        {
            "ticketId": "1",
            "createDT": "2026-01-01 00:00:00",
            "ticket_category": "\u5b50\u5355",
        }
    )

    assert row["ticket_category"] == "\u5b50\u5355"
