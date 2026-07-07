from pathlib import Path

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
