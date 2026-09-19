"""When to retry a model call, and how long to wait.

The free tier rate-limits aggressively and tells us how long to wait when it does, so the
policy honours the server's own delay rather than guessing a longer one.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from google.genai import errors as genai_errors
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.before_sleep import before_sleep_log

from ..errors import LlmError

log = logging.getLogger(__name__)

RETRYABLE_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})
RETRYABLE_STATUSES = frozenset(
    {"RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "ABORTED"}
)
DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")
MAX_WAIT_S = 60.0


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, LlmError):
        return exc.retryable
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.APIError):
        return exc.code in RETRYABLE_CODES or exc.status in RETRYABLE_STATUSES
    return isinstance(exc, httpx.TimeoutException | httpx.TransportError)


def _walk(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        found = [value]
        for item in value.values():
            found.extend(_walk(item))
        return found
    if isinstance(value, list):
        return [entry for item in value for entry in _walk(item)]
    return []


def retry_after_seconds(exc: BaseException) -> float | None:
    """The delay the server asked for, when it carries one."""
    details = getattr(exc, "details", None)
    for entry in _walk(details):
        raw = entry.get("retryDelay") or entry.get("retry_delay")
        if isinstance(raw, str):
            match = DURATION.match(raw.strip())
            if match:
                return float(match.group(1))
    return None


def build_retrying(*, max_attempts: int, base_delay: float) -> Retrying:
    backoff = wait_exponential_jitter(initial=base_delay, max=MAX_WAIT_S)

    def wait(state: RetryCallState) -> float:
        computed = backoff(state)
        outcome = state.outcome
        if outcome is not None and outcome.failed:
            asked = retry_after_seconds(outcome.exception())
            if asked is not None:
                return min(max(computed, asked), MAX_WAIT_S)
        return computed

    return Retrying(
        retry=retry_if_exception(is_retryable),
        wait=wait,
        stop=stop_after_attempt(max_attempts),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
