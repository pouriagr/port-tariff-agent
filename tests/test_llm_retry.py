"""The retry policy: what is worth retrying, and how long to wait."""

from __future__ import annotations

import httpx
import pytest
from google.genai import errors as genai_errors

from port_tariff_agent.errors import LlmError
from port_tariff_agent.llm.retry import build_retrying, is_retryable, retry_after_seconds


def api_error(
    code: int, status: str = "UNKNOWN", details: object | None = None
) -> genai_errors.APIError:
    payload = details if details is not None else {"error": {"code": code, "status": status}}
    return genai_errors.APIError(code, payload)


@pytest.mark.parametrize("code", [408, 409, 429, 500, 502, 503, 504])
def test_transient_status_codes_are_retryable(code: int) -> None:
    assert is_retryable(api_error(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_mistakes_are_not_retryable(code: int) -> None:
    assert not is_retryable(api_error(code))


def test_quota_status_is_retryable_whatever_the_code() -> None:
    assert is_retryable(api_error(0, status="RESOURCE_EXHAUSTED"))


def test_transport_failures_are_retryable() -> None:
    assert is_retryable(httpx.ConnectError("refused"))
    assert is_retryable(httpx.ReadTimeout("slow"))


def test_our_own_error_decides_for_itself() -> None:
    assert is_retryable(LlmError("cut off", retryable=True))
    assert not is_retryable(LlmError("blocked", retryable=False))


def test_unrelated_exceptions_are_not_retryable() -> None:
    assert not is_retryable(ValueError("nope"))


def test_retry_delay_is_read_from_the_error_details() -> None:
    details = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "27s"},
            ],
        }
    }
    assert retry_after_seconds(api_error(429, details=details)) == 27.0


def test_retry_delay_is_none_when_absent() -> None:
    assert retry_after_seconds(api_error(429)) is None
    assert retry_after_seconds(ValueError("x")) is None


def test_retrying_recovers_after_transient_failures() -> None:
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise api_error(429, status="RESOURCE_EXHAUSTED")
        return "ok"

    retrying = build_retrying(max_attempts=5, base_delay=0)
    for attempt in retrying:
        with attempt:
            result = flaky()
    assert result == "ok"
    assert attempts["n"] == 3


def test_retrying_gives_up_on_a_permanent_failure() -> None:
    attempts = {"n": 0}

    def broken() -> None:
        attempts["n"] += 1
        raise api_error(400)

    retrying = build_retrying(max_attempts=5, base_delay=0)
    with pytest.raises(genai_errors.APIError):
        for attempt in retrying:
            with attempt:
                broken()
    assert attempts["n"] == 1


def test_retrying_stops_after_the_attempt_limit() -> None:
    attempts = {"n": 0}

    def always_busy() -> None:
        attempts["n"] += 1
        raise api_error(503)

    retrying = build_retrying(max_attempts=3, base_delay=0)
    with pytest.raises(genai_errors.APIError):
        for attempt in retrying:
            with attempt:
                always_busy()
    assert attempts["n"] == 3
