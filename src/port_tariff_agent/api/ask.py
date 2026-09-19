"""POST /ask: one turn of a conversation."""

from __future__ import annotations

from fastapi import APIRouter

from .deps import AgentFactoryDep, SessionsDep
from .schemas import AskRequest, AskResponse

router = APIRouter(tags=["ask"])


@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask what a vessel call costs",
    description=(
        "Omit `session_id` to start a conversation, or pass the one returned by an earlier"
        " call to ask a follow-up. The agent reads the tariff document itself; every amount"
        " comes with the section and page it was read from and the formula that produced it."
    ),
    responses={
        404: {"description": "The session is unknown or has expired"},
        409: {"description": "That session is already answering a question"},
    },
)
def ask(payload: AskRequest, sessions: SessionsDep, factory: AgentFactoryDep) -> AskResponse:
    # A plain `def`, so Starlette runs this on its worker threadpool: `ask` blocks for as
    # long as the model takes. Errors are mapped centrally, so there is nothing to catch.
    session = (
        sessions.resume(payload.session_id) if payload.session_id else sessions.start(factory())
    )
    with session.claim():
        answer = session.agent.ask(payload.question)
    return AskResponse(session_id=session.session_id, answer=answer)
