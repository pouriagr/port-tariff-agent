"""A tool's arguments are declared from its model, so the rendering has to be faithful."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from port_tariff_agent.agent.answer import TariffAnswer
from port_tariff_agent.llm.schema import json_schema_for


class Leaf(BaseModel):
    name: str = Field(description="what it is called")
    amount: float


class Branch(BaseModel):
    leaves: list[Leaf]
    label: str | None = None
    tags: list[str] = Field(default_factory=list)


def test_a_nested_model_is_inlined() -> None:
    schema = json_schema_for(Branch)
    leaf = schema["properties"]["leaves"]["items"]
    assert leaf["type"] == "object"
    assert leaf["properties"]["name"] == {"type": "string", "description": "what it is called"}
    assert leaf["required"] == ["name", "amount"]


def test_nothing_refers_to_a_definition() -> None:
    rendered = json.dumps(json_schema_for(TariffAnswer))
    assert "$ref" not in rendered
    assert "$defs" not in rendered


def test_an_optional_field_becomes_one_nullable_type() -> None:
    label = json_schema_for(Branch)["properties"]["label"]
    assert label == {"type": "string", "nullable": True}


def test_only_required_fields_are_required() -> None:
    assert json_schema_for(Branch)["required"] == ["leaves"]


def test_property_names_survive() -> None:
    assert set(json_schema_for(Branch)["properties"]) == {"leaves", "label", "tags"}


def test_the_descriptions_the_model_will_read_are_kept() -> None:
    charges = json_schema_for(TariffAnswer)["properties"]["charges"]
    assert charges["description"]
    assert charges["items"]["properties"]["formula"]["description"]


def test_the_answer_schema_is_a_plain_object() -> None:
    schema = json_schema_for(TariffAnswer)
    assert schema["type"] == "object"
    assert schema["required"] == ["answer"]
    assert schema["properties"]["total"]["nullable"] is True
