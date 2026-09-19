"""Ingestion runs as a job, because it costs minutes of model calls (ADR-027).

The job owns its own failure: a background task that raises would leave the caller polling
`running` forever. Progress is the `ProgressFn` the pipeline already emits for the CLI's
progress bar.
"""

from __future__ import annotations

import logging
import secrets
import shutil
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..ingestion.checks import Finding
from ..ingestion.pipeline import IngestResult, ingest_document
from ..llm.protocol import StructuredGenerator
from ..models import DocumentRow
from ..settings import Settings
from .store import BoundedStore, Clock

log = logging.getLogger(__name__)

DEFAULT_MAX_JOBS = 64
DEFAULT_TTL_S = 3600.0
ID_BYTES = 12
QUEUED = "queued"

# `registry.upsert_row` read-modify-writes documents.json and nothing locks the file, so
# two ingests finishing at once could lose a row. One writer at a time, process-wide.
_REGISTRY_LOCK = threading.Lock()


class JobStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class Counts:
    pages: int
    sections: int
    classified: int
    skipped: int
    charges: int


@dataclass(slots=True)
class Job:
    job_id: str
    source: str
    status: JobStatus = JobStatus.RUNNING
    step: str = QUEUED
    done: int = 0
    total: int = 0
    document: DocumentRow | None = None
    already_ingested: bool = False
    counts: Counts | None = None
    findings: list[Finding] = field(default_factory=list)
    error: Exception | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def progress(self, step: str, done: int, total: int) -> None:
        """The pipeline's `ProgressFn`. Locked so a poll never reads a torn triple."""
        with self.lock:
            self.step = step
            self.done = done
            self.total = total

    def succeed(self, result: IngestResult) -> None:
        with self.lock:
            self.status = JobStatus.DONE
            self.document = result.row
            self.already_ingested = result.already_ingested
            self.findings = list(result.findings)
            self.counts = Counts(
                pages=result.page_count,
                sections=result.section_count,
                classified=result.classified,
                skipped=result.skipped,
                charges=result.charge_count,
            )

    def fail(self, exc: Exception) -> None:
        with self.lock:
            self.status = JobStatus.FAILED
            self.error = exc


class JobStore:
    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_JOBS,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Clock | None = None,
    ) -> None:
        kwargs = {} if clock is None else {"clock": clock}
        self._store: BoundedStore[Job] = BoundedStore(
            max_entries=max_entries,
            ttl_s=ttl_s,
            evictable=lambda job: job.status is not JobStatus.RUNNING,
            **kwargs,  # type: ignore[arg-type]
        )

    def start(self, source: str) -> Job:
        job = Job(job_id=secrets.token_urlsafe(ID_BYTES), source=source)
        self._store.add(job.job_id, job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._store.get(job_id)

    def __len__(self) -> int:
        return len(self._store)


def run_ingest_job(
    job: Job, pdf: Path, *, settings: Settings, client: StructuredGenerator, force: bool = False
) -> None:
    """The background task. It never raises: a job records its own outcome."""
    try:
        with _REGISTRY_LOCK:
            result = ingest_document(
                pdf, settings=settings, client=client, force=force, progress=job.progress
            )
        job.succeed(result)
    except Exception as exc:  # a failure belongs on the job, not in an unread traceback
        log.exception("ingest job %s failed", job.job_id)
        job.fail(exc)
    finally:
        shutil.rmtree(pdf.parent, ignore_errors=True)
