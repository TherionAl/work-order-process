from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from work_order_process.api import (
    ApiError,
    WorkOrderClient,
    _declared_item_count,
    _extract_items,
    _has_more,
    _json_or_empty,
    _record_datetime,
    _record_is_since,
)
from work_order_process.config import EndpointConfig, MySQLConfig, Settings


def _settings(
    *,
    methods: list[str] | None = None,
    page_size: int = 2,
    max_pages: int = 3,
) -> Settings:
    return Settings(
        username="user",
        password="password",
        base_url="https://example.invalid/api/v1",
        dictionary_path=Path("unused.pdf"),
        output_dir=Path("unused"),
        page_size=page_size,
        max_pages=max_pages,
        ticket_since="2025-01-01",
        sample_size=3,
        endpoint=EndpointConfig(
            customer_paths=["/companies"],
            contact_paths=["/users"],
            ticket_paths=["/tickets"],
            request_methods=methods or ["GET", "POST"],
        ),
        mysql=MySQLConfig("localhost", 3306, "user", "password", "database"),
    )


def _client(
    handler,
    *,
    methods: list[str] | None = None,
    page_size: int = 2,
    max_pages: int = 3,
) -> WorkOrderClient:
    client = WorkOrderClient(_settings(methods=methods, page_size=page_size, max_pages=max_pages))
    client.client.close()
    client.client = httpx.Client(
        base_url="https://example.invalid/api/v1",
        transport=httpx.MockTransport(handler),
    )
    return client


def test_probe_paths_reports_http_and_business_success_and_failure() -> None:
    requests: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST":
            params = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        requests.append((request.method, request.url.path, params))
        if request.url.path.endswith("/working"):
            return httpx.Response(200, json={"data": [{"id": "C1"}]})
        if request.method == "GET":
            return httpx.Response(404, json={"message": "Invalid resource URI"})
        return httpx.Response(200, json={"errcode": "403", "errmsg": "forbidden"})

    with _client(handler) as client:
        reports = client.probe_paths(["/working", "/missing"])

    assert [(item.path, item.status_code, item.ok) for item in reports] == [
        ("GET /working", 200, True),
        ("POST /working", 200, True),
        ("GET /missing", 404, False),
        ("POST /missing", 200, False),
    ]
    assert requests == [
        ("GET", "/api/v1/working", {"page": "1", "pageSize": "1"}),
        ("POST", "/api/v1/working", {"page": "1", "pageSize": "1"}),
        ("GET", "/api/v1/missing", {"page": "1", "pageSize": "1"}),
        ("POST", "/api/v1/missing", {"page": "1", "pageSize": "1"}),
    ]


def test_fetch_all_falls_back_from_get_to_post_and_from_empty_path() -> None:
    requests: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            params = dict(request.url.params)
        else:
            params = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        requests.append((request.method, request.url.path, params))
        if request.method == "GET":
            return httpx.Response(404, json={"message": "Invalid resource URI"})
        if request.url.path.endswith("/empty"):
            return httpx.Response(200, json={"count": 0, "companies": []})
        page = int(params["page"])
        rows = [{"id": "C1"}, {"id": "C2"}] if page == 1 else [{"id": "C3"}]
        return httpx.Response(200, json={"count": 3, "pageSize": 2, "companies": rows})

    with _client(handler) as client:
        rows = client.fetch_all(["/empty", "/companies"], {"active": "yes"})

    assert rows == [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}]
    post_requests = [item for item in requests if item[0] == "POST"]
    assert [(path, params["page"], params["active"]) for _, path, params in post_requests] == [
        ("/api/v1/empty", "1", "yes"),
        ("/api/v1/companies", "1", "yes"),
        ("/api/v1/companies", "2", "yes"),
    ]


def test_pagination_stops_at_configured_max_pages_when_total_remains() -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        return httpx.Response(
            200,
            json={
                "data": {
                    "total": 99,
                    "pageSize": 2,
                    "records": [{"id": f"{page}-1"}, {"id": f"{page}-2"}],
                }
            },
        )

    with _client(handler, methods=["GET"], max_pages=2) as client:
        rows = client.fetch_all(["/records"])

    assert [row["id"] for row in rows] == ["1-1", "1-2", "2-1", "2-2"]
    assert requested_pages == [1, 2]


def test_detail_and_search_endpoints_extract_domain_values_and_request_parameters() -> None:
    requests: list[tuple[str, dict[str, str]]] = []
    payloads = {
        "/api/v1/users/U1": {"user": [{"uId": "U1", "name": "联系人"}]},
        "/api/v1/companies/C1": {"company": {"cId": "C1", "companyName": "客户"}},
        "/api/v1/tickets/T1": {"ticket": [{"ticketId": "T1", "subject": "故障"}]},
        "/api/v1/supports/S1": {"support": {"sId": "S1", "name": "客服"}},
        "/api/v1/supportgroups/G1": {"supportgroup": [{"sgId": "G1", "sgName": "一线组"}]},
        "/api/v1/tickettemplates/TP1": {
            "tickettemplate": {"tId": "TP1", "ticketTemplateName": "模板"}
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/tickets/search.json"):
            return httpx.Response(
                200,
                json={
                    "tickets": {
                        "count": 1,
                        "results": [{"ticketId": "T1", "subject": "故障"}],
                    }
                },
            )
        return httpx.Response(200, json=payloads[request.url.path])

    with _client(handler, methods=["GET"]) as client:
        assert client.fetch_contact_detail("U1") == {"uId": "U1", "name": "联系人"}
        assert client.fetch_company_detail("C1") == {"cId": "C1", "companyName": "客户"}
        assert client.fetch_ticket_detail("T1") == {"ticketId": "T1", "subject": "故障"}
        assert client.fetch_support_detail("S1") == {"sId": "S1", "name": "客服"}
        assert client.fetch_support_group_detail("G1") == {"sgId": "G1", "sgName": "一线组"}
        assert client.fetch_ticket_template_detail("TP1") == {
            "tId": "TP1",
            "ticketTemplateName": "模板",
        }
        month = client.search_tickets_by_create_month("2026-07", page=2, per_page=25)
        template = client.search_tickets_by_create_month_and_template(
            "2026-07", "TP1", page=3, per_page=10
        )

    assert month["results"][0]["ticketId"] == "T1"
    assert template["count"] == 1
    search_requests = [item for item in requests if item[0].endswith("/tickets/search.json")]
    assert search_requests == [
        (
            "/api/v1/tickets/search.json",
            {
                "query": "createDT:2026-07",
                "sort_by": "createDT",
                "sort_order": "asc",
                "page": "2",
                "per_page": "25",
            },
        ),
        (
            "/api/v1/tickets/search.json",
            {
                "query": "createDT:2026-07 ticketTemplateId:TP1",
                "sort_by": "createDT",
                "sort_order": "asc",
                "page": "3",
                "per_page": "10",
            },
        ),
    ]


def test_field_and_template_envelopes_filter_non_mapping_items_and_cache_fields() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/tickettemplates"):
            return httpx.Response(200, json={"data": [{"tId": "1"}, "bad"]})
        if request.url.path.endswith("/ticket_fields2.json"):
            return httpx.Response(200, json={"fields": [{"id": "field_1"}, None]})
        return httpx.Response(200, json=[{"id": "company_1"}, 7])

    with _client(handler, methods=["GET"]) as client:
        assert client.fetch_ticket_templates() == [{"tId": "1"}]
        assert client.fetch_ticket_fields() == [{"id": "field_1"}]
        assert client.fetch_ticket_fields() == [{"id": "field_1"}]
        assert client.fetch_company_fields() == [{"id": "company_1"}]
        assert client.fetch_company_fields() == [{"id": "company_1"}]

    assert paths.count("/api/v1/tickets/ticket_fields2.json") == 1
    assert paths.count("/api/v1/companies/company_fields.json") == 1


def test_invalid_json_escape_is_repaired_but_unrecoverable_json_is_empty() -> None:
    repaired = httpx.Response(
        200,
        content=b'{"path":"C:\\project\\work\\q","message":"ok"}',
        headers={"content-type": "application/json"},
    )
    broken = httpx.Response(
        200,
        content=b'{"path":"C:\\project",',
        headers={"content-type": "application/json"},
    )

    assert _json_or_empty(repaired) == {"path": r"C:\project\work\q", "message": "ok"}
    assert _json_or_empty(broken) == {}


@pytest.mark.parametrize(
    ("body", "expected_items", "expected_total", "expected_more"),
    [
        ([{"id": 1}, "bad"], [{"id": 1}], 4, True),
        ({"data": {"records": [{"id": 2}], "total": "7"}}, [{"id": 2}], 7, True),
        ({"rows": [], "count": 0}, [], 0, False),
        ("bad", [], 4, False),
    ],
)
def test_pagination_envelopes_are_characterized(
    body, expected_items, expected_total, expected_more
) -> None:
    assert _extract_items(body) == expected_items
    assert _declared_item_count(body, 4) == expected_total
    assert _has_more(body, page=1, item_count=len(expected_items)) is expected_more


@pytest.mark.parametrize(
    ("value", "expected_iso"),
    [
        ("2026-07-01 12:30:45", "2026-07-01T12:30:45"),
        ("2026/07/01", "2026-07-01T00:00:00"),
        ("2026-07-01T09:08:07Z", "2026-07-01T09:08:07"),
        ("not-a-date", None),
        (None, None),
    ],
)
def test_record_timestamps_parse_common_formats(value, expected_iso) -> None:
    parsed = _record_datetime({"createTime": value})
    assert (parsed.isoformat() if parsed else None) == expected_iso


def test_ticket_sample_since_filters_old_records_and_preserves_missing_timestamps() -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        if page == 1:
            rows = [
                {"ticketId": "old", "createDT": "2024-12-31"},
                {"ticketId": "new", "createDT": "2025-01-02"},
            ]
        else:
            rows = [{"ticketId": "unknown"}]
        return httpx.Response(200, json={"count": 3, "tickets": rows})

    with _client(handler, methods=["GET"]) as client:
        sampled = client.fetch_ticket_sample_since(5, "2025-01-01", seed=7)

    assert [row["ticketId"] for row in sampled] == ["new", "unknown"]
    assert _record_is_since({"created_at": "2024-01-01"}, "2025-01-01") is False
    assert _record_is_since({"created_at": ""}, "2025-01-01") is True
    assert set(requested_pages) == {1, 2}


def test_json_detail_helpers_raise_with_response_context() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/http-error"):
            return httpx.Response(503, text="maintenance")
        return httpx.Response(200, json=["not", "an", "object"])

    with _client(handler, methods=["GET"]) as client:
        with pytest.raises(ApiError, match="HTTP 503: maintenance"):
            client._json_get("/http-error")
        with pytest.raises(ApiError, match="Unexpected response"):
            client._json_get("/wrong-shape")


def test_json_repair_preserves_valid_escapes() -> None:
    response = httpx.Response(
        200,
        content=json.dumps({"line": "one\ntwo", "quote": '"ok"'}).encode(),
        headers={"content-type": "application/json"},
    )

    assert _json_or_empty(response) == {"line": "one\ntwo", "quote": '"ok"'}
