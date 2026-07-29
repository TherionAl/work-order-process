"""Structured, redacted failures for import workflows."""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CHINESE_MOBILE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_PASSWORD_ASSIGNMENT_PATTERN = re.compile(
    r"""\bpassword\s*=\s*(?:
        "(?:\\.|[^"])*"
        | '(?:\\.|[^'])*'
        | .*?
    )(?=\s+[A-Za-z_]\w*\s*=|[,;}\]\r\n]|$)""",
    re.IGNORECASE | re.VERBOSE,
)
_SERIALIZED_PAYLOAD_PATTERN = re.compile(
    r"\b(?:payload|request|response|body|data)\s*[:=]\s*(?:\{|\[)",
    re.IGNORECASE,
)
_APPARENT_JSON_START_PATTERN = re.compile(
    r"""
    \{\s*(?:"|}|$)
    |
    \[\s*(?:["{\[\]}]|\d|-|[tfn]|$)
    """,
    re.VERBOSE,
)
_MAX_MESSAGE_LENGTH = 500
_PAYLOAD_REDACTION = "[payload redacted]"


def sanitize_failure_message(exc: BaseException, *, secrets: Iterable[str] = ()) -> str:
    """Return a bounded exception message with common sensitive values removed."""
    return _sanitize_text(str(exc), secrets=secrets)


def _sanitize_text(message: str, *, secrets: Iterable[str]) -> str:
    if _is_payload(message):
        return _PAYLOAD_REDACTION
    unique_secrets = {secret for secret in secrets if secret}
    for secret in sorted(unique_secrets, key=len, reverse=True):
        message = message.replace(secret, "[redacted]")
    message = _PASSWORD_ASSIGNMENT_PATTERN.sub("password=[redacted]", message)
    message = _EMAIL_PATTERN.sub("[email]", message)
    message = _CHINESE_MOBILE_PATTERN.sub("[phone]", message)
    return message[:_MAX_MESSAGE_LENGTH]


def _is_payload(message: str) -> bool:
    inspection_text = message[:_MAX_MESSAGE_LENGTH]
    if _SERIALIZED_PAYLOAD_PATTERN.search(inspection_text):
        return True
    if _APPARENT_JSON_START_PATTERN.search(inspection_text):
        return True
    decoder = json.JSONDecoder()
    for index, character in enumerate(inspection_text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(inspection_text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return True
    return False


@dataclass(frozen=True)
class ImportFailure:
    stage: str
    error_type: str
    safe_message: str
    record_id: str | None = None
    source_row: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "record_id": self.record_id,
            "source_row": self.source_row,
            "error_type": self.error_type,
            "safe_message": self.safe_message,
        }


class FailureCollector:
    def __init__(self, limit: int = 100) -> None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        self.limit = limit
        self.total = 0
        self.failures: list[ImportFailure] = []

    def capture(
        self,
        *,
        stage: str,
        exc: BaseException,
        record_id: object | None = None,
        source_row: int | None = None,
        secrets: Iterable[str] = (),
    ) -> ImportFailure:
        if record_id is None and source_row is None:
            raise ValueError("record_id or source_row is required")
        secret_values = tuple(secrets)
        failure = ImportFailure(
            stage=stage,
            record_id=(
                None if record_id is None else _sanitize_text(str(record_id), secrets=secret_values)
            ),
            source_row=source_row,
            error_type=type(exc).__name__,
            safe_message=sanitize_failure_message(exc, secrets=secret_values),
        )
        self.total += 1
        if len(self.failures) < self.limit:
            self.failures.append(failure)
        return failure

    def as_payload(self) -> dict[str, object]:
        return {
            "failure_count": self.total,
            "failures": [failure.as_dict() for failure in self.failures],
            "failures_truncated": self.total > len(self.failures),
        }
