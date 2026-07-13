from work_order_process.customer_contact_sync import sync_customer_entities


class FakeClient:
    def fetch_companies(self) -> list[dict]:
        return []


class FakeStore:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[tuple[str, str]] = []
        self.saved: list[tuple[str, dict]] = []

    def start_batch(self, entity_type: str) -> str:
        self.started.append(entity_type)
        return "batch-1"

    def finish_batch(self, batch_id: str, status: str, **_: object) -> None:
        self.finished.append((batch_id, status))

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
    def iter_companies(self):
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

    report = sync_customer_entities(None, PagedClient(), sources=["companies"], max_records=2, store=store)

    assert report.status == "success"
    assert report.fetched == 2
    assert report.raw_saved == 2
    assert report.inserted == 2
    assert [len(batch) for batch in store.batches] == [2]
    assert store.saved == []
