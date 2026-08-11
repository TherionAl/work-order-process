import httpx
import pytest

from work_order_process.api_transport import (
    ApiTransportError,
    RetryPolicy,
    request_with_retry,
    retry_delay,
)


class ScriptedHTTPClient:
    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self._outcomes = iter(outcomes)
        self.calls = 0
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    def get(self, path: str, *, params: dict[str, object]) -> httpx.Response:
        return self._request("GET", path, params)

    def post(self, path: str, *, data: dict[str, object]) -> httpx.Response:
        return self._request("POST", path, data)

    def put(self, path: str, *, json: dict[str, object]) -> httpx.Response:
        return self._request("PUT", path, json)

    def _request(self, method: str, path: str, data: dict[str, object]) -> httpx.Response:
        self.calls += 1
        self.requests.append((method, path, data))
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_429_uses_retry_after_then_succeeds() -> None:
    client = ScriptedHTTPClient(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    waits: list[float] = []

    response = request_with_retry(
        client,
        "GET",
        "/tickets",
        {"page": 1},
        sleep=waits.append,
        random_value=lambda: 0.0,
    )

    assert response.status_code == 200
    assert waits == [2.0]
    assert client.calls == 2
    assert client.requests == [
        ("GET", "/tickets", {"page": 1}),
        ("GET", "/tickets", {"page": 1}),
    ]


@pytest.mark.parametrize("status", [401, 403, 404])
def test_permanent_client_errors_are_not_retried(status: int) -> None:
    client = ScriptedHTTPClient([httpx.Response(status)])

    response = request_with_retry(
        client,
        "GET",
        "/tickets",
        {},
        sleep=lambda _: None,
    )

    assert response.status_code == status
    assert client.calls == 1


@pytest.mark.parametrize("status", [502, 503, 504])
def test_transient_server_errors_are_retried(status: int) -> None:
    client = ScriptedHTTPClient([httpx.Response(status), httpx.Response(200)])

    assert (
        request_with_retry(
            client,
            "GET",
            "/tickets",
            {},
            sleep=lambda _: None,
            random_value=lambda: 0.0,
        ).status_code
        == 200
    )
    assert client.calls == 2


def test_transport_error_stops_after_three_attempts() -> None:
    client = ScriptedHTTPClient(
        [
            httpx.ConnectError("down"),
            httpx.ConnectError("down"),
            httpx.ConnectError("down"),
        ]
    )

    with pytest.raises(httpx.ConnectError):
        request_with_retry(client, "GET", "/tickets", {}, sleep=lambda _: None)

    assert client.calls == 3


def test_post_sends_data_as_a_form() -> None:
    client = ScriptedHTTPClient([httpx.Response(200)])

    response = request_with_retry(client, "POST", "/tickets", {"subject": "example"})

    assert response.status_code == 200
    assert client.requests == [("POST", "/tickets", {"subject": "example"})]


def test_put_sends_data_as_json() -> None:
    client = ScriptedHTTPClient([httpx.Response(200)])
    payload = {"ticket": {"custom_fields": [{"key": "field_1447", "value": "4328151"}]}}

    response = request_with_retry(client, "PUT", "/tickets/T1.json", payload)

    assert response.status_code == 200
    assert client.requests == [("PUT", "/tickets/T1.json", payload)]


def test_unsupported_method_is_rejected_without_a_request() -> None:
    client = ScriptedHTTPClient([httpx.Response(200)])

    with pytest.raises(ApiTransportError, match="Unsupported HTTP method: PATCH"):
        request_with_retry(client, "PATCH", "/tickets", {})

    assert client.calls == 0


def test_retry_delay_uses_bounded_exponential_backoff_and_jitter() -> None:
    policy = RetryPolicy(base_delay=2.0, max_delay=3.0, jitter=0.25)

    delay = retry_delay(None, 1, policy, random_value=lambda: 1.0)

    assert delay == 2.5
    assert retry_delay(None, 2, policy, random_value=lambda: 1.0) == 3.0


def test_invalid_retry_after_falls_back_to_backoff() -> None:
    response = httpx.Response(429, headers={"Retry-After": "not-a-number"})

    assert (
        retry_delay(
            response,
            1,
            RetryPolicy(base_delay=2.0, jitter=0.25),
            random_value=lambda: 0.0,
        )
        == 2.0
    )
