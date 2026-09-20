"""The application factory.

Nothing here reads settings, opens a file or builds a model client: the package has to
import with no key in the environment, and configuration is a per-request concern. The
stores hang off `app.state` rather than module globals, so each application owns its own
sessions and jobs and a test cannot inherit another test's.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import ask, documents, health
from .errors import ErrorResponse, register_error_handlers
from .jobs import JobStore
from .sessions import SessionStore

TITLE = "Port Tariff Agent"
SUMMARY = "Find, interpret and compute what a vessel call has to pay, from the tariff itself."

_ERROR_SHAPE = {"model": ErrorResponse}


def create_app(*, sessions: SessionStore | None = None, jobs: JobStore | None = None) -> FastAPI:
    app = FastAPI(
        title=TITLE,
        summary=SUMMARY,
        version=_version(),
        responses={
            422: _ERROR_SHAPE,
            500: _ERROR_SHAPE,
            502: _ERROR_SHAPE,
            503: _ERROR_SHAPE,
        },
    )
    app.state.sessions = sessions if sessions is not None else SessionStore()
    app.state.jobs = jobs if jobs is not None else JobStore()
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(ask.router)
    app.include_router(documents.router)

    @app.get("/", include_in_schema=False)
    def front_door() -> RedirectResponse:
        """The URL a reviewer opens; the interactive documentation is the demo."""
        return RedirectResponse(url="/docs")

    return app


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("port-tariff-agent")
    except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
        return "0"


app = create_app()
