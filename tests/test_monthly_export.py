from work_order_process.monthly_export import (
    _fetch_sample_raw_details,
    export_year_monthly_tickets,
)


class FakeMonthlyClient:
    def search_tickets_by_create_month(self, month_label: str, page: int = 1, per_page: int = 1000):
        rows = [
            {"ticketId": f"{month_label}-1"},
            {"ticketId": f"{month_label}-2"},
            {"ticketId": f"{month_label}-3"},
        ]
        return {"count": len(rows), "results": rows[(page - 1) * per_page : page * per_page]}


class FakeDetailClient:
    def fetch_ticket_detail(self, ticket_id: str):
        return {"ticketId": ticket_id, "subject": f"标题{ticket_id}"}


def test_export_year_monthly_tickets_writes_one_month(tmp_path) -> None:
    report = export_year_monthly_tickets(
        tmp_path,
        FakeMonthlyClient(),
        year=2026,
        months=[6],
        per_page=2,
        show_progress=False,
    )

    output = tmp_path / "2026_monthly_tickets" / "2026-06_tickets.json"
    assert report["ticket_total"] == 3
    assert report["months"][0]["month"] == "2026-06"
    assert output.exists()


def test_fetch_sample_raw_details_keeps_sample_order() -> None:
    rows = [{"ticketId": "3"}, {"ticketId": "1"}, {"ticketId": "2"}]

    details = _fetch_sample_raw_details(rows, FakeDetailClient(), detail_workers=3)

    assert [ticket_id for ticket_id, _ in details] == ["3", "1", "2"]
    assert [detail["ticketId"] for _, detail in details] == ["3", "1", "2"]
