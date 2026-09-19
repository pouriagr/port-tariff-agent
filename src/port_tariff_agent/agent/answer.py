"""The shape of a finished answer.

Structured for the API and for tests, with a free-text field for the chat (ADR-009). The
descriptions are part of the contract: they are what the model is shown when it fills the
`submit_answer` arguments.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChargeLine(BaseModel):
    name: str = Field(description="The charge as the document names it")
    section_id: str = Field(description="The section the rate was read from")
    page_citation: str = Field(description="The page citation returned for that section")
    formula: str = Field(description="The expression that was evaluated, with every constant")
    amount: float = Field(description="The result the calculate tool returned for that formula")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Interpretations the amount depends on, such as the number of services",
    )


class NotApplicableLine(BaseModel):
    section_id: str
    name: str
    reason: str = Field(description="Why this charge does not apply to this call")


class TariffAnswer(BaseModel):
    answer: str = Field(description="The reply to the user, in prose")
    port: str | None = Field(default=None, description="The port the answer is about")
    vessel_summary: str | None = Field(
        default=None, description="The vessel facts the amounts were computed from"
    )
    charges: list[ChargeLine] = Field(
        default_factory=list, description="One entry per charge that applies"
    )
    not_applicable: list[NotApplicableLine] = Field(
        default_factory=list, description="Charges considered and ruled out"
    )
    missing_inputs: list[str] = Field(
        default_factory=list,
        description="Vessel data that was needed and not supplied. Never invent it",
    )
    total: float | None = Field(
        default=None, description="The sum of the amounts, itself produced by the calculate tool"
    )
    currency: str | None = Field(default=None, description="The document's currency")
    notes: list[str] = Field(
        default_factory=list, description="Caveats the user should read, such as taxes excluded"
    )
