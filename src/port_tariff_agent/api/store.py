"""A bounded in-memory store, shared by the session and job registries (ADR-028).

Not a cache: evicting an entry loses a conversation or the record of a finished ingest, so
the bounds are generous and eviction is least-recently-touched first. A clock is injectable
because expiry is the one behaviour a test cannot wait for.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass

Clock = Callable[[], float]


@dataclass(slots=True)
class Entry[T]:
    value: T
    touched_at: float


class BoundedStore[T]:
    """Keyed entries, capped in number and dropped after an idle period."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_s: float,
        clock: Clock = time.monotonic,
        evictable: Callable[[T], bool] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._ttl_s = ttl_s
        self._clock = clock
        self._evictable = evictable or (lambda _value: True)
        self._entries: OrderedDict[str, Entry[T]] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, key: str, value: T) -> T:
        with self._lock:
            self._drop_expired()
            self._entries[key] = Entry(value=value, touched_at=self._clock())
            self._entries.move_to_end(key)
            self._drop_oldest()
        return value

    def get(self, key: str) -> T | None:
        """The value, or None when it is unknown or has been idle for too long."""
        with self._lock:
            self._drop_expired()
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry.touched_at = self._clock()
            self._entries.move_to_end(key)
            return entry.value

    def values(self) -> Iterator[T]:
        with self._lock:
            return iter([entry.value for entry in self._entries.values()])

    def __len__(self) -> int:
        with self._lock:
            self._drop_expired()
            return len(self._entries)

    def _drop_expired(self) -> None:
        cutoff = self._clock() - self._ttl_s
        for key in [
            key
            for key, entry in self._entries.items()
            if entry.touched_at < cutoff and self._evictable(entry.value)
        ]:
            del self._entries[key]

    def _drop_oldest(self) -> None:
        # An entry that is busy is skipped rather than stolen, so a long turn cannot be
        # evicted out from under the request that is running it.
        for key in list(self._entries):
            if len(self._entries) <= self._max_entries:
                return
            if self._evictable(self._entries[key].value):
                del self._entries[key]
