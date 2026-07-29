from __future__ import annotations

import json
from pathlib import Path

from work_order_process.monthly_export import (
    export_month_template_samples,
    export_year_monthly_tickets,
    export_year_monthly_tickets_and_samples,
)


class _Dictionary:
    def translate_record(self, table: str, record: dict) -> dict:
        assert table == "tickets"
        return {"工单号": record["ticketId"], "主题": record.get("subject")}


class _MonthlyClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.searches: list[tuple[str, int, int]] = []

    def search_tickets_by_create_month(
        self, month_label: str, page: int = 1, per_page: int = 1000
    ) -> dict:
        self.searches.append((month_label, page, per_page))
        start = (page - 1) * per_page
        return {
            "count": len(self.rows),
            "results": self.rows[start : start + per_page],
        }


def _monthly_path(output_dir: Path) -> Path:
    return output_dir / "2026_monthly_tickets" / "2026-07_tickets.json"


def test_existing_complete_month_is_reused_without_api_or_file_changes(tmp_path: Path) -> None:
    path = _monthly_path(tmp_path)
    path.parent.mkdir(parents=True)
    report = {
        "month": "2026-07",
        "declared_count": 1,
        "fetched_count": 1,
        "ticket_ids": ["old"],
        "tickets": [{"ticketId": "old", "subject": "保留"}],
        "limit_per_month": None,
    }
    original = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")
    client = _MonthlyClient([{"ticketId": "new"}])

    result = export_year_monthly_tickets(
        tmp_path,
        client,
        year=2026,
        months=[7],
        overwrite=False,
        show_progress=False,
    )

    assert result["months"] == [
        {
            "month": "2026-07",
            "declared_count": 1,
            "fetched_count": 1,
            "ticket_output": str(path),
        }
    ]
    assert client.searches == []
    assert path.read_text(encoding="utf-8") == original


def test_overwrite_replaces_existing_month_with_deterministic_json(tmp_path: Path) -> None:
    path = _monthly_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"stale": true}', encoding="utf-8")
    client = _MonthlyClient(
        [
            {"ticketId": "T2", "subject": "第二"},
            {"ticketId": "T1", "subject": "第一"},
        ]
    )

    result = export_year_monthly_tickets(
        tmp_path,
        client,
        year=2026,
        months=[7],
        per_page=1,
        overwrite=True,
        show_progress=False,
    )

    assert result["ticket_total"] == 2
    assert client.searches == [("2026-07", 1, 1), ("2026-07", 2, 1)]
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "month": "2026-07",
        "declared_count": 2,
        "fetched_count": 2,
        "ticket_ids": ["T2", "T1"],
        "tickets": [
            {"ticketId": "T2", "subject": "第二"},
            {"ticketId": "T1", "subject": "第一"},
        ],
        "limit_per_month": None,
    }
    first_content = path.read_text(encoding="utf-8")

    export_year_monthly_tickets(
        tmp_path,
        _MonthlyClient(client.rows),
        year=2026,
        months=[7],
        per_page=1,
        overwrite=True,
        show_progress=False,
    )
    assert path.read_text(encoding="utf-8") == first_content


def test_empty_month_writes_report_with_empty_ticket_collection(tmp_path: Path) -> None:
    client = _MonthlyClient([])

    report = export_year_monthly_tickets(
        tmp_path,
        client,
        year=2026,
        months=[7],
        show_progress=False,
    )

    assert report["ticket_total"] == 0
    assert client.searches == [("2026-07", 1, 5000)]
    assert json.loads(_monthly_path(tmp_path).read_text(encoding="utf-8")) == {
        "month": "2026-07",
        "declared_count": 0,
        "fetched_count": 0,
        "ticket_ids": [],
        "tickets": [],
        "limit_per_month": None,
    }


class _DetailClient(_MonthlyClient):
    def __init__(self) -> None:
        super().__init__([{"ticketId": "T1"}, {"ticketId": "T2"}])
        self.detail_requests: list[str] = []

    def fetch_ticket_fields(self) -> list[dict]:
        return []

    def fetch_company_fields(self) -> list[dict]:
        return []

    def fetch_ticket_detail(self, ticket_id: str) -> dict | None:
        self.detail_requests.append(ticket_id)
        if ticket_id == "T2":
            return None
        return {"ticketId": ticket_id, "subject": "可用详情", "custom_fields": []}


def test_failed_sample_detail_is_reported_without_discarding_success(tmp_path: Path) -> None:
    client = _DetailClient()

    report = export_year_monthly_tickets_and_samples(
        tmp_path,
        _Dictionary(),
        client,
        year=2026,
        months=[7],
        sample_size=2,
        seed=11,
        detail_workers=1,
        show_progress=False,
    )

    month = report["months"][0]
    assert month["sample_ticket_ids"] == ["T1", "T2"]
    assert month["detail_count"] == 1
    assert month["failed_count"] == 1
    assert month["failed_ids"] == ["T2"]
    assert client.detail_requests == ["T1", "T2"]
    assert json.loads(Path(month["raw_output"]).read_text(encoding="utf-8")) == [
        {"ticketId": "T1", "subject": "可用详情", "custom_fields": []}
    ]
    assert json.loads(Path(month["chinese_output"]).read_text(encoding="utf-8")) == [
        {"工单号": "T1", "主题": "可用详情"}
    ]


class _TemplateClient:
    def __init__(self) -> None:
        self.searches: list[tuple[str, str, int, int]] = []
        self.details: list[str] = []

    def fetch_ticket_templates(self) -> list[dict]:
        return [{"tId": "T1", "ticketTemplateName": "安装模板"}]

    def search_tickets_by_create_month_and_template(
        self, month_label: str, template_id: str, page: int, per_page: int
    ) -> dict:
        self.searches.append((month_label, template_id, page, per_page))
        return {
            "count": 5,
            "results": [{"ticketId": f"{template_id}-{page - 1}"}],
        }

    def fetch_ticket_fields(self) -> list[dict]:
        return []

    def fetch_company_fields(self) -> list[dict]:
        return []

    def fetch_ticket_detail(self, ticket_id: str) -> dict:
        self.details.append(ticket_id)
        return {"ticketId": ticket_id, "subject": "模板样本", "custom_fields": []}


def test_template_sampling_respects_seed_and_sample_size(tmp_path: Path) -> None:
    client = _TemplateClient()

    report = export_month_template_samples(
        tmp_path,
        _Dictionary(),
        client,
        year=2026,
        month=7,
        sample_size=2,
        seed=17,
        detail_workers=1,
        show_progress=False,
    )

    assert report["sample_target_per_template"] == 2
    assert report["sample_ticket_count"] == 2
    assert report["detail_count"] == 2
    assert report["templates"] == [
        {
            "template_id": "T1",
            "template_name": "安装模板",
            "month_count": 5,
            "sample_count": 2,
            "sample_ticket_ids": ["T1-2", "T1-4"],
        }
    ]
    assert client.searches == [
        ("2026-07", "T1", 1, 1),
        ("2026-07", "T1", 3, 1),
        ("2026-07", "T1", 5, 1),
    ]
    assert client.details == ["T1-2", "T1-4"]
    assert [
        row["ticketId"]
        for row in json.loads(Path(report["raw_output"]).read_text(encoding="utf-8"))
    ] == ["T1-2", "T1-4"]
