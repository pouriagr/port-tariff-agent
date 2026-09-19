"""The two in-memory stores: bounds, expiry, locking and the error table.

Expiry is the one behaviour a test cannot wait for, so the clock is injected.
"""

from __future__ import annotations

import pytest

from port_tariff_agent.agent.loop import TariffAgent
from port_tariff_agent.api.errors import (
    JobNotFoundError,
    SessionBusyError,
    SessionNotFoundError,
    UnsupportedUploadError,
    UploadTooLargeError,
    error_info,
    rule_for,
)
from port_tariff_agent.api.jobs import Job, JobStatus, JobStore
from port_tariff_agent.api.sessions import SessionStore
from port_tariff_agent.api.store import BoundedStore
from port_tariff_agent.errors import (
    AgentError,
    CalculationError,
    ClassificationError,
    ConfigError,
    DocumentNotFoundError,
    IngestionError,
    LlmError,
    TranscriptionError,
)


class Clock:
    """A hand-wound monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def agent() -> TariffAgent:
    """An agent that is never asked anything; the store only has to hold it."""
    return TariffAgent(
        client=None,  # type: ignore[arg-type]
        model="model-agent",
        data_dir=None,  # type: ignore[arg-type]
        selector=None,  # type: ignore[arg-type]
    )


def test_a_bounded_store_returns_what_it_was_given() -> None:
    store: BoundedStore[str] = BoundedStore(max_entries=2, ttl_s=10)

    store.add("a", "first")

    assert store.get("a") == "first"
    assert store.get("missing") is None
    assert len(store) == 1


def test_an_idle_entry_expires() -> None:
    clock = Clock()
    store: BoundedStore[str] = BoundedStore(max_entries=4, ttl_s=10, clock=clock)
    store.add("a", "first")

    clock.advance(11)

    assert store.get("a") is None
    assert len(store) == 0


def test_reading_an_entry_keeps_it_alive() -> None:
    clock = Clock()
    store: BoundedStore[str] = BoundedStore(max_entries=4, ttl_s=10, clock=clock)
    store.add("a", "first")

    clock.advance(6)
    assert store.get("a") == "first"
    clock.advance(6)

    assert store.get("a") == "first"


def test_the_least_recently_touched_entry_is_dropped_first() -> None:
    store: BoundedStore[str] = BoundedStore(max_entries=2, ttl_s=1000)
    store.add("a", "first")
    store.add("b", "second")
    store.get("a")

    store.add("c", "third")

    assert store.get("a") == "first"
    assert store.get("b") is None
    assert store.get("c") == "third"


def test_an_entry_in_use_is_not_evicted() -> None:
    store: BoundedStore[str] = BoundedStore(
        max_entries=1, ttl_s=1000, evictable=lambda value: value != "busy"
    )
    store.add("a", "busy")

    store.add("b", "idle")

    assert store.get("a") == "busy"


def test_a_store_must_hold_at_least_one_entry() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        BoundedStore(max_entries=0, ttl_s=1)


def test_a_new_session_gets_an_unguessable_id() -> None:
    store = SessionStore()

    first = store.start(agent())
    second = store.start(agent())

    assert first.session_id != second.session_id
    assert len(first.session_id) > 16
    assert store.resume(first.session_id) is first


def test_resuming_an_unknown_session_is_an_error() -> None:
    with pytest.raises(SessionNotFoundError):
        SessionStore().resume("nothing")


def test_an_expired_session_is_gone_rather_than_stale() -> None:
    clock = Clock()
    store = SessionStore(max_entries=4, ttl_s=10, clock=clock)
    session = store.start(agent())

    clock.advance(11)

    with pytest.raises(SessionNotFoundError):
        store.resume(session.session_id)


def test_a_session_answers_one_question_at_a_time() -> None:
    session = SessionStore().start(agent())

    with session.claim():
        assert session.busy
        with pytest.raises(SessionBusyError), session.claim():
            pass

    assert not session.busy


def test_a_claim_is_released_when_the_turn_fails() -> None:
    session = SessionStore().start(agent())

    with pytest.raises(RuntimeError), session.claim():
        raise RuntimeError("the model gave up")

    assert not session.busy


def test_a_busy_session_survives_the_bound() -> None:
    store = SessionStore(max_entries=1, ttl_s=1000)
    busy = store.start(agent())

    with busy.claim():
        store.start(agent())

    assert store.resume(busy.session_id) is busy


def test_a_job_records_the_progress_the_pipeline_reports() -> None:
    job = Job(job_id="j", source="tariff.pdf")

    job.progress("transcribe", 3, 27)

    assert (job.step, job.done, job.total) == ("transcribe", 3, 27)
    assert job.status is JobStatus.RUNNING


def test_a_failed_job_keeps_its_error() -> None:
    job = Job(job_id="j", source="tariff.pdf")

    job.fail(TranscriptionError([2, 5]))

    assert job.status is JobStatus.FAILED
    assert error_info(job.error).details == {"failed_pages": [2, 5]}  # type: ignore[arg-type]


def test_a_running_job_is_never_evicted() -> None:
    store = JobStore(max_entries=1, ttl_s=1000)
    running = store.start("first.pdf")

    store.start("second.pdf")

    assert store.get(running.job_id) is running


def test_a_finished_job_makes_room() -> None:
    store = JobStore(max_entries=1, ttl_s=1000)
    finished = store.start("first.pdf")
    finished.fail(IngestionError("not a PDF"))

    store.start("second.pdf")

    assert store.get(finished.job_id) is None


def test_an_unknown_job_is_missing() -> None:
    assert JobStore().get("nothing") is None
    with pytest.raises(JobNotFoundError, match="nothing"):
        raise JobNotFoundError("nothing")


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (ConfigError("no key"), 503, "not_configured"),
        (LlmError("quota", retryable=True), 503, "llm_unavailable"),
        (LlmError("garbage"), 502, "llm_failed"),
        (AgentError("gave up"), 502, "agent_gave_up"),
        (TranscriptionError([1]), 500, "transcription_failed"),
        (ClassificationError(["1.1"]), 500, "classification_failed"),
        (IngestionError("unreadable"), 500, "ingestion_failed"),
        (DocumentNotFoundError("none"), 404, "document_not_found"),
        (CalculationError("nope"), 500, "internal"),
        (SessionNotFoundError("s"), 404, "session_not_found"),
        (SessionBusyError("s"), 409, "session_busy"),
        (JobNotFoundError("j"), 404, "job_not_found"),
        (UnsupportedUploadError("notes.txt"), 415, "unsupported_media_type"),
        (UploadTooLargeError(10), 413, "payload_too_large"),
    ],
)
def test_every_error_has_a_status_and_a_stable_code(exc: Exception, status: int, code: str) -> None:
    rule = rule_for(exc)

    assert (rule.status, rule.code) == (status, code)


def test_a_retryable_model_failure_asks_the_caller_to_wait() -> None:
    assert rule_for(LlmError("quota", retryable=True)).retry_after_s is not None
    assert rule_for(LlmError("quota")).retry_after_s is None
