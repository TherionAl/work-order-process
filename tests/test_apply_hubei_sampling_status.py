from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from work_order_process.ticket_writeback import (
    FAILURE_REASON_FIELD_KEY,
    SAMPLING_STATUS_FIELD_KEY,
    SamplingStatusPlan,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "apply_hubei_sampling_status.py"
SPEC = importlib.util.spec_from_file_location("apply_hubei_sampling_status", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
_apply_plan = MODULE._apply_plan


def _audit_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_apply_plan_accepts_an_omitted_text_field_as_verified_clear(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"

    class Client:
        def update_ticket_custom_field(self, ticket_id: str, field_key: str, value: str) -> None:
            assert (ticket_id, field_key, value) == ("T1", FAILURE_REASON_FIELD_KEY, "")

        def fetch_ticket_detail(self, ticket_id: str) -> dict[str, object]:
            return {"custom_fields": []}

    _apply_plan(
        Client(),
        [SamplingStatusPlan("T1", "", "update", "旧原因")],
        FAILURE_REASON_FIELD_KEY,
        audit_path,
        "failure_reason",
        protect_existing=False,
    )

    assert _audit_events(audit_path)[-1]["event"] == "write_verified"


def test_weekly_apply_rechecks_and_preserves_a_value_added_after_preflight(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "audit.jsonl"

    class Client:
        updates: list[tuple[str, str, str]] = []

        def fetch_ticket_detail(self, ticket_id: str) -> dict[str, object]:
            return {"custom_fields": [{"key": SAMPLING_STATUS_FIELD_KEY, "value": "4328151"}]}

        def update_ticket_custom_field(self, ticket_id: str, field_key: str, value: str) -> None:
            self.updates.append((ticket_id, field_key, value))

    client = Client()
    _apply_plan(
        client,
        [SamplingStatusPlan("T1", "4328146", "update", None)],
        SAMPLING_STATUS_FIELD_KEY,
        audit_path,
        "status",
        protect_existing=True,
    )

    assert client.updates == []
    assert _audit_events(audit_path)[-1]["event"] == "skipped_changed_after_preflight"


def test_apply_plan_audits_exhausted_transport_errors(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"

    class Client:
        def update_ticket_custom_field(self, ticket_id: str, field_key: str, value: str) -> None:
            request = httpx.Request("PUT", "https://example.invalid/tickets/T1")
            raise httpx.ConnectError("connection failed", request=request)

    with pytest.raises(RuntimeError, match="回写失败"):
        _apply_plan(
            Client(),
            [SamplingStatusPlan("T1", "4328151", "update", None)],
            SAMPLING_STATUS_FIELD_KEY,
            audit_path,
            "status",
            protect_existing=False,
        )

    event = _audit_events(audit_path)[-1]
    assert event["event"] == "write_error"
    assert event["stage"] == "write"
    assert event["error_type"] == "ConnectError"
