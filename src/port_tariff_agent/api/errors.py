"""Every error becomes HTTP through one table and one handler (ADR-029).

Routes contain no try/except: a handler registered on `PortTariffError` catches every
subclass, because Starlette looks a handler up along the exception's method resolution
order. Failures that are purely HTTP concerns subclass `ApiError` here instead, so the
domain hierarchy the CLI also uses stays free of web vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from ..errors import (
    AgentError,
    ClassificationError,
    ConfigError,
    DocumentNotFoundError,
    IngestionError,
    LlmError,
    PortTariffError,
    TranscriptionError,
)

RETRY_AFTER_S = 30


class ApiError(Exception):
    """A failure that only exists because there is an HTTP layer."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details


class SessionNotFoundError(ApiError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"Unknown or expired session {session_id!r}."
            " Omit session_id to start a new conversation.",
        )


class SessionBusyError(ApiError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            "This session is already answering a question. Wait for it to finish.",
            details={"session_id": session_id},
        )


class JobNotFoundError(ApiError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Unknown or expired job {job_id!r}.")


class UnsupportedUploadError(ApiError):
    def __init__(self, filename: str) -> None:
        super().__init__("Upload a PDF file in the `file` field.", details={"filename": filename})


class UploadTooLargeError(ApiError):
    def __init__(self, limit_bytes: int) -> None:
        super().__init__(
            f"The upload exceeds the {limit_bytes} byte limit.",
            details={"limit_bytes": limit_bytes},
        )


class ErrorInfo(BaseModel):
    code: str = Field(description="A stable identifier a client can branch on")
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


@dataclass(frozen=True, slots=True)
class Rule:
    status: int
    code: str
    retry_after_s: int | None = None


# Most specific first: the first class the exception is an instance of wins.
RULES: tuple[tuple[type[Exception], Rule], ...] = (
    (ConfigError, Rule(503, "not_configured")),
    (TranscriptionError, Rule(500, "transcription_failed")),
    (ClassificationError, Rule(500, "classification_failed")),
    (IngestionError, Rule(500, "ingestion_failed")),
    (DocumentNotFoundError, Rule(404, "document_not_found")),
    (AgentError, Rule(502, "agent_gave_up")),
    (LlmError, Rule(502, "llm_failed")),
    (SessionNotFoundError, Rule(404, "session_not_found")),
    (SessionBusyError, Rule(409, "session_busy")),
    (JobNotFoundError, Rule(404, "job_not_found")),
    (UnsupportedUploadError, Rule(415, "unsupported_media_type")),
    (UploadTooLargeError, Rule(413, "payload_too_large")),
    (PortTariffError, Rule(500, "internal")),
    (ApiError, Rule(500, "internal")),
)


def rule_for(exc: Exception) -> Rule:
    if isinstance(exc, LlmError) and exc.retryable:
        return Rule(503, "llm_unavailable", retry_after_s=RETRY_AFTER_S)
    return next(rule for cls, rule in RULES if isinstance(exc, cls))


def details_for(exc: Exception) -> dict[str, Any] | None:
    if isinstance(exc, TranscriptionError):
        return {"failed_pages": exc.failed_pages}
    if isinstance(exc, ClassificationError):
        return {"section_ids": exc.section_ids}
    if isinstance(exc, ApiError):
        return exc.details
    return None


def error_info(exc: Exception) -> ErrorInfo:
    """The envelope, also used for the `error` field of a failed ingest job."""
    rule = rule_for(exc)
    return ErrorInfo(code=rule.code, message=str(exc), details=details_for(exc))


def _render(exc: Exception) -> JSONResponse:
    rule = rule_for(exc)
    headers = {"Retry-After": str(rule.retry_after_s)} if rule.retry_after_s is not None else None
    body = ErrorResponse(error=error_info(exc))
    return JSONResponse(body.model_dump(mode="json"), status_code=rule.status, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    """One handler per family. Anything else is a bug and stays a traceback."""

    async def handled(_request: Request, exc: Exception) -> JSONResponse:
        return _render(exc)

    async def http_error(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, HTTPException)
        body = ErrorResponse(error=ErrorInfo(code="http_error", message=str(exc.detail)))
        return JSONResponse(
            body.model_dump(mode="json"), status_code=exc.status_code, headers=exc.headers
        )

    async def invalid_request(_request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        body = ErrorResponse(
            error=ErrorInfo(
                code="invalid_request",
                message="The request body is not valid.",
                details={"errors": exc.errors()},
            )
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=422)

    app.add_exception_handler(PortTariffError, handled)
    app.add_exception_handler(ApiError, handled)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, invalid_request)
