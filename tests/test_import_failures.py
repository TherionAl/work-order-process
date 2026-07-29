import json
from dataclasses import FrozenInstanceError

import pytest

import work_order_process.import_failures as import_failures
from work_order_process.import_failures import (
    FailureCollector,
    sanitize_failure_message,
)


def test_failure_collector_redacts_sensitive_values_and_limits_details() -> None:
    collector = FailureCollector(limit=2)
    collector.capture(
        stage="parse",
        source_row=2,
        exc=ValueError("password=secret-value email=user@example.com phone=13800138000"),
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


def test_failure_summary_limit_zero_counts_and_truncates_without_details() -> None:
    collector = FailureCollector(limit=0)

    failure = collector.capture(
        stage="database",
        record_id="T1",
        exc=RuntimeError("deadlock"),
    )

    assert failure.safe_message == "deadlock"
    assert collector.as_payload() == {
        "failure_count": 1,
        "failures": [],
        "failures_truncated": True,
    }


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


def test_capture_sanitizes_record_id_with_the_same_boundary_as_messages() -> None:
    collector = FailureCollector()

    failure = collector.capture(
        stage="database",
        record_id=(
            'customer=user@example.com phone=13800138000 password="my secret" token=api-secret'
        ),
        exc=RuntimeError("deadlock"),
        secrets=("api-secret",),
    )

    assert failure.record_id is not None
    assert "user@example.com" not in failure.record_id
    assert "13800138000" not in failure.record_id
    assert "my secret" not in failure.record_id
    assert "api-secret" not in failure.record_id
    assert "password=[redacted]" in failure.record_id


def test_sanitize_failure_message_replaces_structured_payloads() -> None:
    safe_message = sanitize_failure_message(
        ValueError('{"token":"secret-value","records":[{"id":1}]}'),
        secrets=("secret-value",),
    )

    assert safe_message == "[payload redacted]"


def test_sanitize_failure_message_replaces_long_serialized_payloads() -> None:
    safe_message = sanitize_failure_message(
        ValueError("response={'token': 'secret-value'} " + "x" * 500),
        secrets=("secret-value",),
    )

    assert safe_message == "[payload redacted]"


def test_sanitize_failure_message_redacts_quoted_and_delimited_passwords() -> None:
    safe_message = sanitize_failure_message(
        ValueError('password="my secret"; password=second-secret, password=third-secret'),
        secrets=(),
    )

    assert "my" not in safe_message
    assert "secret" not in safe_message
    assert "second-secret" not in safe_message
    assert "third-secret" not in safe_message
    assert safe_message.count("password=[redacted]") == 3


def test_sanitize_failure_message_redacts_overlapping_secrets_longest_first() -> None:
    safe_message = sanitize_failure_message(
        ValueError("token=secret-value"),
        secrets=("value", "secret-value", "value"),
    )

    assert safe_message == "token=[redacted]"


def test_capture_reuses_iterable_secrets_for_record_id_and_message() -> None:
    collector = FailureCollector()

    failure = collector.capture(
        stage="api",
        record_id="T1",
        exc=ValueError("token=generator-secret"),
        secrets=(secret for secret in ("generator-secret",)),
    )

    assert "generator-secret" not in failure.safe_message


def test_capture_replaces_short_labelled_record_id_payload() -> None:
    collector = FailureCollector()

    failure = collector.capture(
        stage="api",
        record_id="response={'token':'x'}",
        exc=RuntimeError("timeout"),
    )

    assert failure.record_id == "[payload redacted]"


def test_sanitize_failure_message_replaces_embedded_json_payload() -> None:
    safe_message = sanitize_failure_message(
        ValueError('API returned {"token":"x"}'),
    )

    assert safe_message == "[payload redacted]"


@pytest.mark.parametrize(
    "payload",
    (
        '{"token":"secret","padding":"' + "x" * 600 + '"}',
        '["secret","' + "x" * 600 + '"]',
    ),
)
def test_sanitize_failure_message_replaces_embedded_json_closing_after_limit(
    payload: str,
) -> None:
    safe_message = sanitize_failure_message(
        ValueError("API returned " + payload),
    )

    assert safe_message == "[payload redacted]"


def test_sanitize_failure_message_keeps_ordinary_diagnostic_prose() -> None:
    safe_message = sanitize_failure_message(
        ValueError(
            "connection timeout after 3 seconds; "
            "missing field [customer_id]; template {name} is unavailable"
        ),
    )

    assert safe_message == (
        "connection timeout after 3 seconds; "
        "missing field [customer_id]; template {name} is unavailable"
    )


def test_payload_inspection_bounds_decoder_attempts_to_persisted_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class CountingDecoder:
        def raw_decode(self, text: str) -> tuple[object, int]:
            nonlocal calls
            calls += 1
            raise json.JSONDecodeError("invalid", text, 0)

    monkeypatch.setattr(import_failures.json, "JSONDecoder", CountingDecoder)

    assert import_failures._is_payload("[invalid]" * 10_000) is False
    assert calls <= 500
