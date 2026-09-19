"""Test doubles for the model boundary."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from port_tariff_agent.llm.protocol import (
    InlineFile,
    LlmRequest,
    LlmResult,
    Message,
    ModelTurn,
    ToolSpec,
)

Responder = Callable[[LlmRequest], BaseModel]
TurnResponder = Callable[[list[Message]], ModelTurn]


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


class FakeToolCallingLlm:
    """A scripted conversation: one ModelTurn per call, in order.

    Each turn may instead be a callable taking the history, for a test that has to react to
    what the agent sent.
    """

    def __init__(self, turns: Sequence[ModelTurn | TurnResponder]) -> None:
        self._turns = list(turns)
        self.calls: list[dict[str, object]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def histories(self) -> list[list[Message]]:
        return [list(call["history"]) for call in self.calls]  # type: ignore[arg-type]

    def last_history(self) -> list[Message]:
        return self.histories[-1]

    def generate_with_tools(
        self,
        *,
        call_id: str,
        model: str,
        system_instruction: str,
        history: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        self.calls.append(
            {
                "call_id": call_id,
                "model": model,
                "system_instruction": system_instruction,
                "history": list(history),
                "tool_names": [tool.name for tool in tools],
            }
        )
        if not self._turns:
            raise AssertionError(f"FakeToolCallingLlm ran out of turns at {call_id!r}")
        turn = self._turns.pop(0)
        return turn(list(history)) if callable(turn) else turn
