"""The HTTP surface, end to end against scripted responses.

Offline like the rest of the suite: both composition seams are overridden, so no real
client is ever built. `TestClient` runs a background task to completion before the response
is returned, so an ingest job is finished by the time the 202 arrives and the lifecycle can
be asserted without waiting for anything.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from port_tariff_agent.agent.loop import TariffAgent
from port_tariff_agent.agent.selector import ChargeSelector, Selection, SelectorResponse
from port_tariff_agent.api import create_app
from port_tariff_agent.api.deps import (
    provide_agent_factory,
    provide_client,
    provide_optional_settings,
    provide_settings,
)
from port_tariff_agent.api.sessions import SessionStore
from port_tariff_agent.errors import ConfigError, LlmError
from port_tariff_agent.llm.protocol import LlmRequest, Message, ModelTurn, ToolCall, UserMessage
from port_tariff_agent.registry import load_registry
from port_tariff_agent.settings import Settings
from tests.conftest import Document
from tests.fakes import CombinedFake, FakeLlm
from tests.test_agent import ANSWER
from tests.test_pipeline import responder

pytestmark = pytest.mark.integration


def get_charges_turn(document: Document) -> ModelTurn:
    return ModelTurn(
        tool_calls=(
            ToolCall(
                name="get_charges",
                args={
                    "port": document.port,
                    "vessel_description": "A vessel",
                    "arrival_date": "2024-06-01",
                },
            ),
        )
    )


def submit_turn() -> ModelTurn:
    return ModelTurn(tool_calls=(ToolCall(name="submit_answer", args=ANSWER),))


def conversation(document: Document, turns: int = 1) -> CombinedFake:
    """A fake that can answer `turns` questions: fetch the charges, then submit."""
    return CombinedFake(
        [turn for _ in range(turns) for turn in (get_charges_turn(document), submit_turn())],
        SelectorResponse(applicable=[Selection(section_id="1.2", reason="x")]),
    )


def agent_for(document: Document, client: object, **kwargs: object) -> TariffAgent:
    return TariffAgent(
        client=client,  # type: ignore[arg-type]
        model="model-agent",
        data_dir=document.data_dir,
        selector=ChargeSelector(client, model="model-extract"),  # type: ignore[arg-type]
        today=date(2024, 6, 1),
        **kwargs,  # type: ignore[arg-type]
    )


def make_client(
    *,
    settings: Settings | None = None,
    agent_factory: Callable[[], TariffAgent] | None = None,
    llm: object | None = None,
    sessions: SessionStore | None = None,
    unconfigured: bool = False,
) -> TestClient:
    """One application per test, so stores and overrides are never shared."""
    app = create_app(sessions=sessions)
    if settings is not None:
        app.dependency_overrides[provide_settings] = lambda: settings
        app.dependency_overrides[provide_optional_settings] = lambda: settings
    if unconfigured:

        def missing() -> Settings:
            raise ConfigError("Missing or invalid configuration: GEMINI_API_KEY.")

        app.dependency_overrides[provide_settings] = missing
        app.dependency_overrides[provide_optional_settings] = lambda: None
    if agent_factory is not None:
        app.dependency_overrides[provide_agent_factory] = lambda: agent_factory
    if llm is not None:
        app.dependency_overrides[provide_client] = lambda: llm
    return TestClient(app)


@pytest.fixture
def asking(settings: Settings, document: Document) -> tuple[TestClient, CombinedFake]:
    client = conversation(document, turns=2)
    api = make_client(
        settings=settings.model_copy(update={"data_dir": document.data_dir}),
        agent_factory=lambda: agent_for(document, client),
    )
    return api, client


def test_health_reports_what_the_service_can_answer(settings: Settings, document: Document) -> None:
    api = make_client(settings=settings.model_copy(update={"data_dir": document.data_dir}))

    body = api.get("/health").json()

    assert body == {
        "status": "ok",
        "configured": True,
        "documents": 1,
        "sessions": 0,
        "jobs": 0,
    }


def test_health_says_degraded_instead_of_failing_when_unconfigured() -> None:
    api = make_client(unconfigured=True)

    response = api.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["configured"] is False
    assert response.json()["documents"] is None


def test_asking_starts_a_session_and_returns_the_answer(
    asking: tuple[TestClient, CombinedFake],
) -> None:
    api, _ = asking

    response = api.post("/ask", json={"question": "What does my vessel pay?"})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["answer"]["answer"] == "Two charges apply."
    assert body["answer"]["total"] == 1282.5
    assert body["answer"]["charges"][0]["section_id"] == "1.2"


def test_a_follow_up_reuses_the_same_conversation(
    asking: tuple[TestClient, CombinedFake],
) -> None:
    api, llm = asking
    session_id = api.post("/ask", json={"question": "What does my vessel pay?"}).json()[
        "session_id"
    ]

    response = api.post(
        "/ask", json={"question": "Why is the arrival fee that high?", "session_id": session_id}
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    asked = [message.text for message in llm.last_history() if isinstance(message, UserMessage)]
    assert asked == ["What does my vessel pay?", "Why is the arrival fee that high?"]


def test_the_session_count_on_health_follows_the_conversations(
    asking: tuple[TestClient, CombinedFake],
) -> None:
    api, _ = asking

    api.post("/ask", json={"question": "What does my vessel pay?"})

    assert api.get("/health").json()["sessions"] == 1


def test_an_unknown_session_is_refused_rather_than_silently_replaced(
    settings: Settings, document: Document
) -> None:
    api = make_client(
        settings=settings,
        agent_factory=lambda: agent_for(document, conversation(document)),
    )

    response = api.post("/ask", json={"question": "Hello", "session_id": "gone"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_a_session_already_answering_is_not_queued_behind(
    settings: Settings, document: Document
) -> None:
    sessions = SessionStore()
    api = make_client(
        settings=settings,
        agent_factory=lambda: agent_for(document, conversation(document)),
        sessions=sessions,
    )
    session_id = api.post("/ask", json={"question": "What does my vessel pay?"}).json()[
        "session_id"
    ]

    with sessions.resume(session_id).claim():
        response = api.post("/ask", json={"question": "Again", "session_id": session_id})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_busy"
    assert response.json()["error"]["details"] == {"session_id": session_id}


def test_asking_without_configuration_says_which_variable_is_missing() -> None:
    api = make_client(unconfigured=True)

    response = api.post("/ask", json={"question": "Hello"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_configured"
    assert "GEMINI_API_KEY" in response.json()["error"]["message"]


def test_a_question_must_not_be_empty(settings: Settings) -> None:
    api = make_client(settings=settings)

    response = api.post("/ask", json={"question": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_rate_limited_model_asks_the_caller_to_retry(
    settings: Settings, document: Document
) -> None:
    def out_of_quota(_history: list[Message]) -> ModelTurn:
        raise LlmError("Out of quota", retryable=True)

    client = CombinedFake([out_of_quota], SelectorResponse(applicable=[]))
    api = make_client(settings=settings, agent_factory=lambda: agent_for(document, client))

    response = api.post("/ask", json={"question": "What does my vessel pay?"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"
    assert response.headers["Retry-After"]


def test_an_agent_that_never_answers_is_reported_as_a_bad_gateway(
    settings: Settings, document: Document
) -> None:
    client = CombinedFake(
        [ModelTurn(text="thinking"), ModelTurn(text="still thinking")],
        SelectorResponse(applicable=[]),
    )
    api = make_client(
        settings=settings,
        agent_factory=lambda: agent_for(document, client, max_iterations=1),
    )

    response = api.post("/ask", json={"question": "What does my vessel pay?"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_gave_up"


def test_uploading_a_document_returns_a_job_that_finishes(
    settings: Settings, tiny_pdf: Callable[[int], Path]
) -> None:
    llm = FakeLlm(default=responder)
    api = make_client(settings=settings, llm=llm)
    pdf = tiny_pdf(3)

    accepted = api.post("/documents", files={"file": (pdf.name, pdf.read_bytes())})

    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert accepted.json()["status"] == "running"
    assert accepted.headers["Location"] == f"/documents/jobs/{job_id}"

    job = api.get(f"/documents/jobs/{job_id}").json()
    assert job["status"] == "done"
    assert job["error"] is None
    assert job["already_ingested"] is False
    assert job["counts"] == {
        "pages": 3,
        "sections": 5,
        "classified": 5,
        "skipped": 0,
        "charges": 2,
    }
    assert job["document"]["ports"] == ["Alpha Bay", "Bravo Point"]
    assert len(load_registry(settings.data_dir)) == 1


def test_uploading_the_same_document_twice_costs_no_model_calls(
    settings: Settings, tiny_pdf: Callable[[int], Path]
) -> None:
    llm = FakeLlm(default=responder)
    api = make_client(settings=settings, llm=llm)
    pdf = tiny_pdf(3)
    api.post("/documents", files={"file": (pdf.name, pdf.read_bytes())})
    after_first = llm.call_count

    accepted = api.post("/documents", files={"file": (pdf.name, pdf.read_bytes())})

    job = api.get(f"/documents/jobs/{accepted.json()['job_id']}").json()
    assert job["status"] == "done"
    assert job["already_ingested"] is True
    assert llm.call_count == after_first


def test_a_page_that_cannot_be_read_fails_the_job_not_the_upload(
    settings: Settings, tiny_pdf: Callable[[int], Path]
) -> None:
    def broken(request: LlmRequest) -> object:
        if request.call_id == "transcribe/page_002":
            raise LlmError("page blew up", retryable=False)
        return responder(request)

    api = make_client(settings=settings, llm=FakeLlm(default=broken))
    pdf = tiny_pdf(3)

    accepted = api.post("/documents", files={"file": (pdf.name, pdf.read_bytes())})

    assert accepted.status_code == 202
    job = api.get(f"/documents/jobs/{accepted.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert job["error"]["code"] == "transcription_failed"
    assert job["error"]["details"] == {"failed_pages": [2]}
    assert load_registry(settings.data_dir) == []


def test_only_a_pdf_can_be_ingested(settings: Settings) -> None:
    api = make_client(settings=settings)

    response = api.post("/documents", files={"file": ("notes.txt", b"not a pdf")})

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert api.get("/health").json()["jobs"] == 0


def test_an_empty_upload_is_rejected(settings: Settings) -> None:
    api = make_client(settings=settings)

    response = api.post("/documents", files={"file": ("empty.pdf", b"")})

    assert response.status_code == 415


def test_an_upload_over_the_limit_is_rejected(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from port_tariff_agent.api import documents as documents_module

    monkeypatch.setattr(documents_module, "MAX_UPLOAD_BYTES", 8)
    api = make_client(settings=settings)

    response = api.post("/documents", files={"file": ("big.pdf", b"%PDF-1.4 and then some")})

    assert response.status_code == 413
    assert response.json()["error"]["details"] == {"limit_bytes": 8}


def test_an_unknown_job_is_not_found(settings: Settings) -> None:
    api = make_client(settings=settings)

    response = api.get("/documents/jobs/nothing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_the_document_list_names_the_ports_that_can_be_priced(
    settings: Settings, document: Document
) -> None:
    api = make_client(settings=settings.model_copy(update={"data_dir": document.data_dir}))

    rows = api.get("/documents").json()

    assert len(rows) == 1
    assert rows[0]["document_hash"] == document.row.document_hash
    assert rows[0]["ports"] == [document.port]


def test_the_package_serves_an_app_without_configuration() -> None:
    """`uvicorn port_tariff_agent.api:app` must import with no key in the environment."""
    from port_tariff_agent.api import app as application

    assert application.title
