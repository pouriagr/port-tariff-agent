"""The ReAct loop (ADR-007, ADR-010).

The agent reads the conversation, calls tools until it has what it needs, and ends the
turn by submitting an answer. History is kept whole, so a follow-up question can reuse the
section texts already in it without fetching them again.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from ..errors import AgentError
from ..llm.protocol import Message, ModelTurn, ToolCallingGenerator, ToolResult, UserMessage
from ..prompts import TARIFF_AGENT
from .answer import TariffAnswer
from .selector import ChargeSelector
from .tools import SUBMIT_ANSWER, Toolbox

log = logging.getLogger(__name__)

MAX_ITERATIONS = 20
NUDGE = (
    "Continue by calling a tool. When the answer is ready, deliver it with"
    f" {SUBMIT_ANSWER}; do not write it as prose."
)
LAST_CALL = (
    "You have reached the limit on steps for this question. Call"
    f" {SUBMIT_ANSWER} now with what you have, and say in `notes` that the answer may be"
    " incomplete."
)


class TariffAgent:
    """One conversation. Call `ask` as often as the user has questions."""

    def __init__(
        self,
        *,
        client: ToolCallingGenerator,
        model: str,
        data_dir: Path,
        selector: ChargeSelector,
        max_iterations: int = MAX_ITERATIONS,
        today: date | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._tools = Toolbox(data_dir=data_dir, selector=selector, today=today)
        self._max_iterations = max_iterations
        self.history: list[Message] = []

    def ask(self, message: str) -> TariffAnswer:
        self.history.append(UserMessage(text=message))
        for step in range(self._max_iterations):
            answer = self._step(step)
            if answer is not None:
                return answer

        self.history.append(UserMessage(text=LAST_CALL))
        answer = self._step(self._max_iterations, tools_allowed=(SUBMIT_ANSWER,))
        if answer is None:
            raise AgentError(
                f"The agent did not produce an answer in {self._max_iterations} steps."
            )
        return answer

    def _step(self, step: int, tools_allowed: Sequence[str] | None = None) -> TariffAnswer | None:
        specs = [
            spec
            for spec in self._tools.specs
            if tools_allowed is None or spec.name in tools_allowed
        ]
        turn = self._client.generate_with_tools(
            call_id=f"agent/{step}",
            model=self._model,
            system_instruction=TARIFF_AGENT.text,
            history=self.history,
            tools=specs,
        )
        self.history.append(turn)
        if not turn.tool_calls:
            log.debug("step %d produced no tool call", step)
            self.history.append(UserMessage(text=NUDGE))
            return None
        return self._run_tools(turn)

    def _run_tools(self, turn: ModelTurn) -> TariffAnswer | None:
        answer: TariffAnswer | None = None
        for call in turn.tool_calls:
            log.info("tool %s", call.name)
            outcome = self._tools.run(call)
            self.history.append(ToolResult(name=call.name, payload=outcome.payload))
            answer = answer or outcome.answer
        return answer
