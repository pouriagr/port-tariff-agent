"""Ingesting a document, and reading what has been ingested."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Response, UploadFile, status

from ..models import DocumentRow
from ..registry import load_registry
from .deps import ClientDep, JobsDep, SettingsDep
from .errors import JobNotFoundError, UnsupportedUploadError, UploadTooLargeError
from .jobs import run_ingest_job
from .schemas import JobAccepted, JobResponse, job_response

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
PDF_SUFFIX = ".pdf"

router = APIRouter(tags=["documents"])


def _stash(upload: UploadFile) -> Path:
    """Copy the upload somewhere the background task can still read it.

    An `UploadFile` is closed when the request ends and the work outlives the request. The
    name is reduced to its basename first: it is supplied by the caller, and the pipeline
    records it as the registry row's `source`.
    """
    name = Path(upload.filename or "").name
    if not name.lower().endswith(PDF_SUFFIX):
        raise UnsupportedUploadError(upload.filename or "")

    directory = Path(tempfile.mkdtemp(prefix="port-tariff-upload-"))
    target = directory / name
    written = 0
    try:
        with target.open("wb") as stream:
            while chunk := upload.file.read(CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError(MAX_UPLOAD_BYTES)
                stream.write(chunk)
        if written == 0:
            raise UnsupportedUploadError(name)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return target


@router.post(
    "/documents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    summary="Ingest a tariff PDF",
    description=(
        "Reading a tariff book is tens of model calls, so the upload is accepted and the"
        " work runs in the background. Poll the URL in the `Location` header for progress."
        " A document that is already ingested finishes immediately and says so."
    ),
    responses={
        413: {"description": "The upload is larger than the limit"},
        415: {"description": "The upload is not a PDF"},
    },
)
def ingest(
    response: Response,
    background: BackgroundTasks,
    settings: SettingsDep,
    client: ClientDep,
    jobs: JobsDep,
    file: Annotated[UploadFile, File(description="The tariff PDF to ingest")],
    force: bool = False,
) -> JobAccepted:
    pdf = _stash(file)
    job = jobs.start(pdf.name)
    background.add_task(run_ingest_job, job, pdf, settings=settings, client=client, force=force)
    response.headers["Location"] = f"/documents/jobs/{job.job_id}"
    return JobAccepted(job_id=job.job_id, status=job.status)


@router.get(
    "/documents/jobs/{job_id}",
    response_model=JobResponse,
    summary="Progress of an ingestion",
    responses={404: {"description": "The job is unknown or has expired"}},
)
def job_status(job_id: str, jobs: JobsDep) -> JobResponse:
    job = jobs.get(job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    return job_response(job)


@router.get(
    "/documents",
    response_model=list[DocumentRow],
    summary="Documents available to answer questions",
    description="Which ports and validity periods this service can price a call for.",
)
def documents(settings: SettingsDep) -> list[DocumentRow]:
    return load_registry(settings.data_dir)
