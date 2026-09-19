"""The three tools the agent drives, and the dispatch that runs them.

Each tool declares its arguments from the same model its handler validates against
(ADR-021). A handler never raises at the agent: a bad argument comes back as an `error`
payload, which is a turn the model can recover from.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..errors import CalculationError
from ..llm.protocol import ToolCall, ToolSpec
from ..llm.schema import json_schema_for
from .answer import TariffAnswer
from .calculator import evaluate
from .knowledge import get_charges
from .selector import ChargeSelector

log = logging.getLogger(__name__)

GET_CHARGES = "get_charges"
CALCULATE = "calculate"
SUBMIT_ANSWER = "submit_answer"

MONEY_DIGITS = 2


class GetChargesArgs(BaseModel):
    port: str = Field(description="The port the vessel is calling at")
    vessel_description: str = Field(
        description="Everything known about the vessel and the call, in prose"
    )
    arrival_date: str | None = Field(
        default=None, description="Date of arrival as YYYY-MM-DD, if it is known"
    )


class CalculateArgs(BaseModel):
    expression: str = Field(
        description="Arithmetic over literal numbers, with + - * / ( ) and the functions"
        " ceil, floor, round, min and max. No names, no units."
    )


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    payload: dict[str, Any]
    answer: TariffAnswer | None = None


def _specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=GET_CHARGES,
            description=(
                "Return the charges this document defines for a port, the text of the ones"
                " that apply to this vessel call, and the sections stating the terms needed"
                " to read them. Call it once per port and date in question."
            ),
            parameters=json_schema_for(GetChargesArgs),
        ),
        ToolSpec(
            name=CALCULATE,
            description=(
                "Evaluate one arithmetic expression and return the result. Every amount you"
                " report must come from this tool."
            ),
            parameters=json_schema_for(CalculateArgs),
        ),
        ToolSpec(
            name=SUBMIT_ANSWER,
            description="Deliver the finished answer. This ends the turn.",
            parameters=json_schema_for(TariffAnswer),
        ),
    ]


class Toolbox:
    """Holds what the tools need, so the loop only has to route calls by name."""

    def __init__(
        self, *, data_dir: Path, selector: ChargeSelector, today: date | None = None
    ) -> None:
        self._data_dir = data_dir
        self._selector = selector
        self._today = today or date.today()
        self.specs = _specs()

    def run(self, call: ToolCall) -> ToolOutcome:
        handlers = {
            GET_CHARGES: self._get_charges,
            CALCULATE: self._calculate,
            SUBMIT_ANSWER: self._submit,
        }
        handler = handlers.get(call.name)
        if handler is None:
            return ToolOutcome({"error": f"There is no tool called {call.name!r}"})
        return handler(call.args)

    def _get_charges(self, args: Mapping[str, Any]) -> ToolOutcome:
        try:
            parsed = GetChargesArgs.model_validate(dict(args))
        except ValidationError as exc:
            return ToolOutcome(_invalid(GET_CHARGES, exc))

        try:
            arrival = (
                date.fromisoformat(parsed.arrival_date) if parsed.arrival_date else self._today
            )
        except ValueError:
            return ToolOutcome(
                {"error": f"{parsed.arrival_date!r} is not a date. Use the format YYYY-MM-DD."}
            )

        return ToolOutcome(
            get_charges(
                data_dir=self._data_dir,
                selector=self._selector,
                port=parsed.port,
                vessel_description=parsed.vessel_description,
                arrival_date=arrival,
            )
        )

    def _calculate(self, args: Mapping[str, Any]) -> ToolOutcome:
        try:
            parsed = CalculateArgs.model_validate(dict(args))
        except ValidationError as exc:
            return ToolOutcome(_invalid(CALCULATE, exc))

        try:
            raw = evaluate(parsed.expression)
        except CalculationError as exc:
            return ToolOutcome({"error": str(exc), "expression": parsed.expression})
        return ToolOutcome(
            {"expression": parsed.expression, "result": round(raw, MONEY_DIGITS), "raw": raw}
        )

    def _submit(self, args: Mapping[str, Any]) -> ToolOutcome:
        try:
            answer = TariffAnswer.model_validate(dict(args))
        except ValidationError as exc:
            return ToolOutcome(_invalid(SUBMIT_ANSWER, exc))
        return ToolOutcome({"accepted": True}, answer=answer)


def _invalid(name: str, exc: ValidationError) -> dict[str, Any]:
    """Validation errors are flattened to strings: the payload has to survive a round trip
    through the provider's JSON, which a raw error context does not."""
    log.debug("%s rejected the arguments: %s", name, exc)
    details = [
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(include_url=False, include_input=False)
    ]
    return {
        "error": f"The arguments for {name} did not validate. Fix them and call it again.",
        "details": details,
    }
