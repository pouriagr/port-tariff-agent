"""Request and response bodies.

`TariffAnswer` and `DocumentRow` are reused as they are. `TariffAnswer`'s field
descriptions are the `submit_answer` tool schema the model is shown (ADR-021), so API prose
belongs here and on the routes, never in `agent/answer.py`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, Field

from ..agent.answer import TariffAnswer
from ..ingestion.checks import Finding, Level
from ..models import DocumentRow
from .errors import ErrorInfo, error_info
from .jobs import Counts, Job, JobStatus

MAX_QUESTION_CHARS = 8000


class AskRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=MAX_QUESTION_CHARS,
        description="The vessel call to price, in prose or as pasted vessel data",
    )
    session_id: str | None = Field(
        default=None, description="Omit to start a new conversation; pass one to continue it"
    )


class AskResponse(BaseModel):
    session_id: str = Field(description="Pass this back to ask a follow-up question")
    answer: TariffAnswer


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus


class Progress(BaseModel):
    step: str
    done: int
    total: int


class CountsResponse(BaseModel):
    pages: int
    sections: int
    classified: int
    skipped: int
    charges: int


class FindingLine(BaseModel):
    level: Level
    code: str
    message: str


class JobResponse(BaseModel):
    job_id: str
    source: str
    status: JobStatus
    progress: Progress
    document: DocumentRow | None = None
    already_ingested: bool = False
    counts: CountsResponse | None = None
    findings: list[FindingLine] = Field(default_factory=list)
    error: ErrorInfo | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    configured: bool = Field(description="False when the environment is missing a variable")
    documents: int | None = Field(
        default=None, description="Ingested documents, or null when unconfigured"
    )
    sessions: int
    jobs: int


def _counts(counts: Counts | None) -> CountsResponse | None:
    return None if counts is None else CountsResponse(**asdict(counts))


def _finding(finding: Finding) -> FindingLine:
    return FindingLine(level=finding.level, code=finding.code, message=finding.message)


def job_response(job: Job) -> JobResponse:
    """A snapshot of a job. `IngestResult.paths` is server-side and never leaves."""
    with job.lock:
        return JobResponse(
            job_id=job.job_id,
            source=job.source,
            status=job.status,
            progress=Progress(step=job.step, done=job.done, total=job.total),
            document=job.document,
            already_ingested=job.already_ingested,
            counts=_counts(job.counts),
            findings=[_finding(finding) for finding in job.findings],
            error=None if job.error is None else error_info(job.error),
        )
