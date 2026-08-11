"""HTTP request transport with bounded retries for transient failures."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


class ApiTransportError(RuntimeError):
    """Raised when a request cannot be sent through the supported transport."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retry_statuses: frozenset[int] = frozenset({429, 502, 503, 504})
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.25


DEFAULT_RETRY_POLICY = RetryPolicy()


def retry_delay(
    response: httpx.Response | None,
    attempt: int,
    policy: RetryPolicy,
    random_value: Callable[[], float],
) -> float:
    """Return a server-directed delay or a bounded exponential backoff."""

    if response is not None:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            return retry_after

    exponential = policy.base_delay * (2 ** max(0, attempt - 1))
    bounded = min(policy.max_delay, exponential)
    return min(policy.max_delay, bounded * (1 + policy.jitter * random_value()))


def request_with_retry(
    client: httpx.Client,
    method: str,
    path: str,
    data: dict[str, Any],
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> httpx.Response:
    """Send a GET, form POST, or JSON PUT with bounded transient retries."""

    if method not in {"GET", "POST", "PUT"}:
        raise ApiTransportError(f"Unsupported HTTP method: {method}")

    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = _send_request(client, method, path, data)
        except httpx.TransportError:
            if attempt == policy.max_attempts:
                raise
            sleep(retry_delay(None, attempt, policy, random_value))
            continue

        if response.status_code not in policy.retry_statuses:
            return response
        if attempt == policy.max_attempts:
            return response
        sleep(retry_delay(response, attempt, policy, random_value))

    raise ApiTransportError("Retry policy must allow at least one attempt.")


def _send_request(
    client: httpx.Client, method: str, path: str, data: dict[str, Any]
) -> httpx.Response:
    if method == "GET":
        return client.get(path, params=data)
    if method == "POST":
        return client.post(path, data=data)
    return client.put(path, json=data)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds
