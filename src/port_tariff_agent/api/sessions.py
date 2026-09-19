"""One conversation per session id, held in memory (ADR-028).

A session is a live `TariffAgent`, because the history carries opaque provider signatures
that have to round-trip unchanged. `ask` mutates that history, so a session answers one
question at a time.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..agent.loop import TariffAgent
from .errors import SessionBusyError, SessionNotFoundError
from .store import BoundedStore, Clock

DEFAULT_MAX_SESSIONS = 32
DEFAULT_TTL_S = 3600.0
ID_BYTES = 16


@dataclass(slots=True)
class Session:
    session_id: str
    agent: TariffAgent
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def busy(self) -> bool:
        return self.lock.locked()

    @contextmanager
    def claim(self) -> Iterator[None]:
        """Hold the session for one turn, or refuse rather than queue behind it."""
        if not self.lock.acquire(blocking=False):
            raise SessionBusyError(self.session_id)
        try:
            yield
        finally:
            self.lock.release()


class SessionStore:
    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_SESSIONS,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Clock | None = None,
    ) -> None:
        kwargs = {} if clock is None else {"clock": clock}
        self._store: BoundedStore[Session] = BoundedStore(
            max_entries=max_entries,
            ttl_s=ttl_s,
            evictable=lambda session: not session.busy,
            **kwargs,  # type: ignore[arg-type]
        )

    def start(self, agent: TariffAgent) -> Session:
        """A new conversation. The id is random because it grants access to the history."""
        session = Session(session_id=secrets.token_urlsafe(ID_BYTES), agent=agent)
        self._store.add(session.session_id, session)
        return session

    def resume(self, session_id: str) -> Session:
        session = self._store.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def __len__(self) -> int:
        return len(self._store)
