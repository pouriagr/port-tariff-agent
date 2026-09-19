"""Schemas for everything written to disk.

These are the contract between ingestion and the query phase, so they live at the package
root rather than inside `ingestion`.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Payer(StrEnum):
    VESSEL = "vessel"
    CARGO_OWNER = "cargo_owner"
    OTHER = "other"


class SectionNode(BaseModel):
    id: str
    title: str
    parent: str | None = None
    children: list[str] = Field(default_factory=list)
    order: int
    pdf_page: int | None = None
    printed_page: int | None = None
    text: str = ""

    @property
    def depth(self) -> int:
        return self.id.count(".") + 1


class TariffIndexFile(BaseModel):
    document_hash: str = Field(
        validation_alias=AliasChoices("document_hash", "hash"),
        serialization_alias="document_hash",
    )
    sections: list[SectionNode] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class Charge(BaseModel):
    section_id: str
    name: str
    payer: Payer | None = None
    applies_when: str | None = None


class ChargesFile(BaseModel):
    document_hash: str = Field(
        validation_alias=AliasChoices("document_hash", "hash"),
        serialization_alias="document_hash",
    )
    prompt_version: int
    model: str
    charges: list[Charge] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ClassificationRecord(BaseModel):
    """One cached classifier answer, positive or negative."""

    section_id: str
    defines_charge: bool
    charge_name: str | None = None
    payer: Payer | None = None
    applies_when: str | None = None
    ports_mentioned: list[str] = Field(default_factory=list)
    section_text_sha: str
    prompt_version: int
    prompt_sha: str
    model: str


class DocumentProfile(BaseModel):
    issuer: str | None = None
    title: str | None = None
    currency: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None


class DocumentRow(BaseModel):
    document_hash: str = Field(
        validation_alias=AliasChoices("document_hash", "hash"),
        serialization_alias="document_hash",
    )
    source: str
    issuer: str | None = None
    title: str | None = None
    currency: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    ports: list[str] = Field(default_factory=list)
    page_count: int
    ingested_at: str
    active: bool = True

    model_config = ConfigDict(populate_by_name=True)


class StepRecord(BaseModel):
    prompt_version: int | None = None
    prompt_sha: str | None = None
    model: str | None = None
    completed_at: str | None = None


class Manifest(BaseModel):
    document_hash: str = Field(
        validation_alias=AliasChoices("document_hash", "hash"),
        serialization_alias="document_hash",
    )
    steps: dict[str, StepRecord] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    def matches(self, step: str, *, prompt_version: int, prompt_sha: str, model: str) -> bool:
        record = self.steps.get(step)
        return (
            record is not None
            and record.prompt_version == prompt_version
            and record.prompt_sha == prompt_sha
            and record.model == model
        )

    def record(self, step: str, *, prompt_version: int, prompt_sha: str, model: str) -> None:
        self.steps[step] = StepRecord(
            prompt_version=prompt_version,
            prompt_sha=prompt_sha,
            model=model,
            completed_at=datetime.now().astimezone().isoformat(),
        )
