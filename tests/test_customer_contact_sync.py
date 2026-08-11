from types import SimpleNamespace

import pytest

from work_order_process import customer_contact_sync
from work_order_process.customer_contact_sync import (
    sync_contact_entities,
    sync_customer_entities,
)


def test_mysql_store_connects_without_creating_schema(monkeypatch) -> None:
    connection = object()
    monkeypatch.setattr(
        customer_contact_sync,
        "ensure_mysql_schema",
        lambda config: pytest.fail("ordinary entity sync must not create schema"),
    )
    monkeypatch.setattr(
        customer_contact_sync,
        "_pymysql",
        lambda: SimpleNamespace(connect=lambda **kwargs: connection),
    )

    store = customer_contact_sync.MySQLCustomerContactStore(
        SimpleNamespace(
            host="db",
            port=3306,
            user="user",
            password="secret",
            database="warehouse",
        )
    )

    assert store.connection is connection


class FakeClient:
    def fetch_companies(self) -> list[dict]:
        return []


class FakeStore:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[tuple[str, str]] = []
        self.finish_details: list[dict[str, object]] = []
        self.saved: list[tuple[str, dict]] = []

    def start_batch(self, entity_type: str) -> str:
        self.started.append(entity_type)
        return "batch-1"

    def finish_batch(self, batch_id: str, status: str, **details: object) -> None:
        self.finished.append((batch_id, status))
        self.finish_details.append(details)

    def save_entity(self, **kwargs: object) -> str:
        self.saved.append((str(kwargs["entity_type"]), dict(kwargs["row"])))
        return "inserted"

    def close(self) -> None:
        return None


def test_empty_customer_result_fails_batch_without_writes() -> None:
    store = FakeStore()

    report = sync_customer_entities(None, FakeClient(), sources=["companies"], store=store)

    assert report.status == "failed"
    assert report.fetched == 0
    assert report.inserted == 0
    assert store.started == ["customer"]
    assert store.finished == [("batch-1", "failed")]
    assert store.saved == []


class PagedClient:
    def iter_companies(self, _extra_params=None):
        yield [{"uId": "C1", "companyName": "One"}, {"uId": "C2", "companyName": "Two"}]
        yield [{"uId": "C3", "companyName": "Three"}]


class FakeBulkStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[list[dict]] = []

    def save_entities(self, **kwargs: object) -> dict[str, int]:
        records = list(kwargs["records"])
        self.batches.append(records)
        return {"raw_saved": len(records), "inserted": len(records), "changed": 0, "unchanged": 0}


def test_customer_sync_uses_paged_bulk_writes_and_respects_limit() -> None:
    store = FakeBulkStore()

    report = sync_customer_entities(
        None, PagedClient(), sources=["companies"], max_records=2, store=store
    )

    assert report.status == "success"
    assert report.fetched == 2
    assert report.raw_saved == 2
    assert report.inserted == 2
    assert [len(batch) for batch in store.batches] == [2]
    assert store.saved == []


class InvalidRecordClient:
    def iter_companies(self, _extra_params=None):
        yield [{"uId": "", "companyName": "missing stable id"}]


def test_invalid_customer_is_failed_with_safe_reason() -> None:
    store = FakeStore()

    report = sync_customer_entities(
        None,
        InvalidRecordClient(),
        sources=["companies"],
        store=store,
    )

    assert report.status == "partial"
    assert report.failed == 1
    assert report.failures[0]["stage"] == "prepare"
    assert report.failures[0]["source_row"] == 1
    assert report.failures[0]["error_type"]
    error_message = str(store.finish_details[0]["error_message"])
    assert "prepare ValueError" in error_message
    assert "\n" not in error_message


class BulkFailsThenOneRowFailsStore(FakeStore):
    def save_entities(self, **_: object) -> dict[str, int]:
        raise RuntimeError("bulk write failed")

    def save_entity(self, **kwargs: object) -> str:
        if kwargs["row"]["customer_id"] == "C2":
            raise RuntimeError("row write failed")
        return "inserted"


def test_bulk_fallback_records_only_final_row_failure() -> None:
    report = sync_customer_entities(
        None,
        PagedClient(),
        sources=["companies"],
        store=BulkFailsThenOneRowFailsStore(),
    )

    assert report.inserted == 2
    assert report.failed == 1
    assert [failure["record_id"] for failure in report.failures] == ["C2"]
    assert report.failures[0]["stage"] == "database"


class LaterPageFailureClient:
    def iter_companies(self, _extra_params=None):
        yield [
            {"uId": "C1", "companyName": "One"},
            {"uId": "", "companyName": "missing stable id"},
        ]
        raise RuntimeError("later page failed")


def test_later_api_failure_preserves_prior_entity_counts_and_failures() -> None:
    store = FakeStore()

    report = sync_customer_entities(
        None,
        LaterPageFailureClient(),
        sources=["companies"],
        store=store,
    )

    assert report.status == "failed"
    assert report.fetched == 2
    assert report.raw_saved == 1
    assert report.inserted == 1
    assert report.changed == 0
    assert report.unchanged == 0
    assert report.failed == 2
    assert len(report.failures) == report.failed
    assert [failure["stage"] for failure in report.failures] == ["prepare", "api"]


class ContactClient:
    def iter_contacts(self, _extra_params=None):
        yield [
            {"cId": "U1", "name": "张三", "companyId": "C1"},
            {"cId": "U1", "name": "重复记录", "companyId": "C1"},
        ]


def test_contact_source_uses_contact_shape_and_deduplicates_stable_id() -> None:
    store = FakeBulkStore()

    report = sync_contact_entities(
        None,
        ContactClient(),
        sources=["company_contacts"],
        store=store,
    )

    assert report.status == "success"
    assert report.fetched == 2
    assert report.inserted == 1
    assert len(store.batches) == 1
    assert [item["row"]["contact_id"] for item in store.batches[0]] == ["U1"]
    assert store.batches[0][0]["source_name"] == "company_contacts"
