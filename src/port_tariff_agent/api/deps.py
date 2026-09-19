"""What the routes depend on, and the two seams a test replaces.

Settings are resolved per request, not at import: the package must import with no key in
the environment, and a missing variable should be a 503 with the message that says which
one, not a crash at start-up.

Tests swap `provide_agent_factory` and `provide_client` through
`app.dependency_overrides`. Patching the module attribute would not work: `Depends(f)`
captures the function object when the route is registered.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Annotated

from fastapi import Depends, Request

from ..agent.factory import build_agent
from ..agent.loop import TariffAgent
from ..errors import ConfigError
from ..llm.client import GeminiClient
from ..llm.protocol import LlmClient
from ..settings import Settings, get_settings
from .jobs import JobStore
from .sessions import SessionStore

AgentFactory = Callable[[], TariffAgent]


def provide_settings() -> Settings:
    """Raises `ConfigError`, which the error handler renders as 503 not_configured."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(provide_settings)]


def provide_optional_settings() -> Settings | None:
    """None instead of an error, for the one endpoint that reports being unconfigured."""
    try:
        return provide_settings()
    except ConfigError:
        return None


OptionalSettingsDep = Annotated[Settings | None, Depends(provide_optional_settings)]


def provide_client(settings: SettingsDep) -> LlmClient:
    """Constructing the client opens no connection, so one per request is fine."""
    return GeminiClient(settings)


def provide_agent_factory(settings: SettingsDep) -> AgentFactory:
    """A factory, not an agent: one is built only when a session is created."""
    return partial(build_agent, settings)


def provide_sessions(request: Request) -> SessionStore:
    return request.app.state.sessions  # type: ignore[no-any-return]


def provide_jobs(request: Request) -> JobStore:
    return request.app.state.jobs  # type: ignore[no-any-return]


ClientDep = Annotated[LlmClient, Depends(provide_client)]
AgentFactoryDep = Annotated[AgentFactory, Depends(provide_agent_factory)]
SessionsDep = Annotated[SessionStore, Depends(provide_sessions)]
JobsDep = Annotated[JobStore, Depends(provide_jobs)]
