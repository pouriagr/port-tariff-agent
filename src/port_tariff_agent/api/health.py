"""GET /health: what can be checked without a key and without a model call."""

from __future__ import annotations

from fastapi import APIRouter

from ..registry import load_registry
from .deps import JobsDep, OptionalSettingsDep, SessionsDep
from .schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness, and what the service can answer",
    description=(
        "Reports whether configuration loaded and how many documents are ingested. It never"
        " calls a model. Being unconfigured is reported as `degraded` with a 200, because a"
        " probe that fails hides the message that says what is wrong."
    ),
)
def health(settings: OptionalSettingsDep, sessions: SessionsDep, jobs: JobsDep) -> HealthResponse:
    documents: int | None = None
    if settings is not None:
        try:
            documents = len(load_registry(settings.data_dir))
        except OSError:
            documents = None
    return HealthResponse(
        status="ok" if documents is not None else "degraded",
        configured=settings is not None,
        documents=documents,
        sessions=len(sessions),
        jobs=len(jobs),
    )
