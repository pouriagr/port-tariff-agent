"""Test doubles for the model boundary."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from port_tariff_agent.llm.protocol import InlineFile, LlmRequest, LlmResult

Responder = Callable[[LlmRequest], BaseModel]


class FakeLlm:
    """Scripted responses, with a record of everything that was asked.

    `responses` maps a call id to either a model instance or a callable; `default` covers
    anything not named.
    """

    def __init__(
        self,
        responses: dict[str, BaseModel | Responder] | None = None,
        *,
        default: BaseModel | Responder | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self.requests: list[LlmRequest] = []
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    @property
    def call_ids(self) -> list[str]:
        return [request.call_id for request in self.requests]

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def request_for(self, call_id: str) -> LlmRequest:
        return next(request for request in self.requests if request.call_id == call_id)

    def generate_structured[T: BaseModel](
        self,
        *,
        call_id: str,
        model: str,
        schema: type[T],
        prompt: str,
        files: Sequence[InlineFile] = (),
        max_output_tokens: int | None = None,
    ) -> LlmResult[T]:
        request = LlmRequest(call_id=call_id, model=model, prompt=prompt, files=tuple(files))
        with self._lock:
            self.requests.append(request)
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            answer = self._responses.get(call_id, self._default)
            if answer is None:
                raise AssertionError(f"FakeLlm has no response scripted for {call_id!r}")
            value = answer(request) if callable(answer) else answer
            if not isinstance(value, schema):
                raise AssertionError(
                    f"scripted response for {call_id!r} is not a {schema.__name__}"
                )
            return LlmResult(value=value, model=model)
        finally:
            with self._lock:
                self._in_flight -= 1
