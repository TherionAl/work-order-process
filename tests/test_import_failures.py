import pytest

from dataclasses import FrozenInstanceError

from work_order_process.import_failures import (
    FailureCollector,
    ImportFailure,
    sanitize_failure_message,
)


def test_failure_collector_redacts_sensitive_values_and_limits_details() -> None:
    collector = FailureCollector(limit=2)
    collector.capture(
        stage="parse",
        source_row=2,
        exc=ValueError(
            "password=secret-value email=user@example.com phone=13800138000"
        ),
        secrets=("secret-value",),
    )
    collector.capture(stage="database", record_id="T2", exc=RuntimeError("deadlock"))
    collector.capture(stage="api", record_id="T3", exc=RuntimeError("timeout"))

    payload = collector.as_payload()

    assert payload["failure_count"] == 3
    assert payload["failures_truncated"] is True
    assert len(payload["failures"]) == 2
    serialized = repr(payload)
    assert "secret-value" not in serialized
    assert "user@example.com" not in serialized
    assert "13800138000" not in serialized


def test_failure_requires_record_id_or_source_row() -> None:
    collector = FailureCollector()

    with pytest.raises(ValueError, match="record_id or source_row"):
        collector.capture(stage="parse", exc=ValueError("bad"))


def test_sanitize_failure_message_redacts_and_bounds_output() -> None:
    safe_message = sanitize_failure_message(
        ValueError("token=abc password=super-secret user@example.com 13800138000 " + "x" * 600),
        secrets=("abc", "super-secret"),
    )

    assert "abc" not in safe_message
    assert "super-secret" not in safe_message
    assert "user@example.com" not in safe_message
    assert "13800138000" not in safe_message
    assert "[redacted]" in safe_message
    assert "[email]" in safe_message
    assert "[phone]" in safe_message
    assert len(safe_message) == 500


def test_capture_returns_immutable_serializable_failure() -> None:
    collector = FailureCollector()

    failure = collector.capture(stage="api", record_id=7, exc=RuntimeError("timeout"))

    assert failure.as_dict() == {
        "stage": "api",
        "record_id": "7",
        "source_row": None,
        "error_type": "RuntimeError",
        "safe_message": "timeout",
    }
    with pytest.raises(FrozenInstanceError):
        failure.stage = "database"  # type: ignore[misc]
