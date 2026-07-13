from pathlib import Path

import httpx

from work_order_process.api import WorkOrderClient
from work_order_process.config import EndpointConfig, MySQLConfig, Settings


def _settings() -> Settings:
    return Settings(
        username="user",
        password="password",
        base_url="https://example.invalid/api/v1",
        dictionary_path=Path("unused"),
        output_dir=Path("unused"),
        page_size=100,
        max_pages=10,
        ticket_since="2025-01-01",
        sample_size=3,
        endpoint=EndpointConfig(
            customer_paths=["/companies"],
            contact_paths=["/users"],
            ticket_paths=["/tickets"],
            request_methods=["GET"],
        ),
        mysql=MySQLConfig(
            host="127.0.0.1",
            port=3306,
            user="root",
            password="",
            database="work_order",
        ),
    )


def test_fetch_support_detail_uses_client_cache(monkeypatch) -> None:
    client = WorkOrderClient(_settings())
    calls: list[str] = []

    def fake_json_get(path: str, params=None):
        calls.append(path)
        return {"support": {"sId": "10", "name": "张三"}}

    monkeypatch.setattr(client, "_json_get", fake_json_get)

    first = client.fetch_support_detail("10")
    first["name"] = "被调用方修改"
    second = client.fetch_support_detail("10")

    assert calls == ["/supports/10"]
    assert second == {"sId": "10", "name": "张三"}
    client.close()


def test_fetch_ticket_template_detail_caches_missing_result(monkeypatch) -> None:
    client = WorkOrderClient(_settings())
    calls: list[str] = []

    def fake_json_get(path: str, params=None):
        calls.append(path)
        return {"tickettemplate": []}

    monkeypatch.setattr(client, "_json_get", fake_json_get)

    assert client.fetch_ticket_template_detail("104") is None
    assert client.fetch_ticket_template_detail("104") is None
    assert calls == ["/tickettemplates/104"]
    client.close()


def test_probe_entity_paths_reports_nonempty_and_empty_results(monkeypatch) -> None:
    client = WorkOrderClient(_settings())

    def fake_fetch(path: str, params: dict) -> httpx.Response:
        assert params["page"] == 1
        assert params["pageSize"] == 2
        if path == "/companies":
            return httpx.Response(200, json={"count": 7, "companies": [{"uId": "C1", "companyName": "Example"}]})
        return httpx.Response(200, json={"count": 0, "companies": []})

    monkeypatch.setattr(client, "_first_successful_request", fake_fetch)

    report = client.probe_entity_paths(["/companies", "/customers"], "customer", sample_size=2)

    assert report == [
        {"path": "/companies", "entity_type": "customer", "status": "ok", "count": 7, "sample_keys": ["companyName", "uId"]},
        {"path": "/customers", "entity_type": "customer", "status": "empty", "count": 0, "sample_keys": []},
    ]
    client.close()


def test_iter_entity_pages_yields_each_page_without_collecting_all_rows(monkeypatch) -> None:
    client = WorkOrderClient(_settings())
    calls: list[int] = []

    def fake_fetch(path: str, params: dict) -> httpx.Response:
        calls.append(params["page"])
        if params["page"] == 1:
            return httpx.Response(200, json={"count": 3, "pageSize": 2, "companies": [{"uId": "C1"}, {"uId": "C2"}]})
        return httpx.Response(200, json={"count": 3, "pageSize": 2, "companies": [{"uId": "C3"}]})

    monkeypatch.setattr(client, "_first_successful_request", fake_fetch)

    assert list(client.iter_entity_pages(["/companies"], page_size=2)) == [
        [{"uId": "C1"}, {"uId": "C2"}],
        [{"uId": "C3"}],
    ]
    assert calls == [1, 2]
    client.close()


def test_iter_entity_pages_uses_declared_total_when_api_ignores_requested_page_size(monkeypatch) -> None:
    client = WorkOrderClient(_settings())
    calls: list[int] = []

    def fake_fetch(path: str, params: dict) -> httpx.Response:
        calls.append(params["page"])
        if params["page"] == 1:
            return httpx.Response(200, json={"count": 101, "companies": [{"uId": str(index)} for index in range(100)]})
        return httpx.Response(200, json={"count": 101, "companies": [{"uId": "100"}]})

    monkeypatch.setattr(client, "_first_successful_request", fake_fetch)

    assert [len(page) for page in client.iter_entity_pages(["/companies"], page_size=5000)] == [100, 1]
    assert calls == [1, 2]
    client.close()
