"""The selector's answer is checked against the document before anything is read."""

from __future__ import annotations

from port_tariff_agent.agent.selector import ChargeSelector, Selection, SelectorResponse
from port_tariff_agent.models import Charge, Payer, SectionNode
from tests.fakes import FakeLlm

CHARGES = [
    Charge(section_id="1.2", name="Arrival Fee", payer=Payer.VESSEL, applies_when="On arrival"),
    Charge(section_id="1.3", name="Mooring Fee", payer=Payer.VESSEL, applies_when="Per service"),
]
SECTIONS = [
    SectionNode(id="1.1", title="GENERAL", parent="1", order=1, text="Working hours are stated."),
]


def selector(response: SelectorResponse) -> tuple[ChargeSelector, FakeLlm]:
    llm = FakeLlm(default=response)
    return ChargeSelector(llm, model="model-extract"), llm


def run(response: SelectorResponse) -> tuple[SelectorResponse, FakeLlm]:
    chooser, llm = selector(response)
    result = chooser.select(
        charges=CHARGES, sections=SECTIONS, port="Northaven", vessel_description="A vessel"
    )
    return result, llm


def test_it_returns_what_the_model_chose() -> None:
    result, _ = run(
        SelectorResponse(
            applicable=[Selection(section_id="1.2", reason="it arrives")],
            context_sections=[Selection(section_id="1.1", reason="working hours")],
        )
    )
    assert [item.section_id for item in result.applicable] == ["1.2"]
    assert [item.section_id for item in result.context_sections] == ["1.1"]


def test_an_id_the_document_does_not_have_is_dropped() -> None:
    result, _ = run(
        SelectorResponse(
            applicable=[
                Selection(section_id="9.9", reason="invented"),
                Selection(section_id="1.3", reason="real"),
            ]
        )
    )
    assert [item.section_id for item in result.applicable] == ["1.3"]


def test_a_context_section_that_is_a_charge_is_allowed() -> None:
    """A charge section may also carry the terms; only repetition is dropped."""
    result, _ = run(
        SelectorResponse(context_sections=[Selection(section_id="1.2", reason="defines a term")])
    )
    assert [item.section_id for item in result.context_sections] == ["1.2"]


def test_a_repeated_id_is_returned_once() -> None:
    result, _ = run(
        SelectorResponse(
            applicable=[
                Selection(section_id="1.2", reason="first"),
                Selection(section_id="1.2", reason="again"),
            ],
            context_sections=[Selection(section_id="1.2", reason="and again")],
        )
    )
    assert [item.section_id for item in result.applicable] == ["1.2"]
    assert result.context_sections == []


def test_surrounding_space_in_an_id_is_tolerated() -> None:
    result, _ = run(SelectorResponse(applicable=[Selection(section_id=" 1.2 ", reason="spaced")]))
    assert [item.section_id for item in result.applicable] == ["1.2"]


def test_the_prompt_carries_the_catalog_the_sections_the_port_and_the_vessel() -> None:
    _, llm = run(SelectorResponse())
    prompt = llm.requests[0].prompt
    assert "1.2 | Arrival Fee" in prompt
    assert "1.1 | GENERAL" in prompt
    assert "Northaven" in prompt
    assert "A vessel" in prompt


def test_the_call_is_made_with_the_extraction_model() -> None:
    _, llm = run(SelectorResponse())
    assert llm.requests[0].model == "model-extract"
    assert llm.call_ids == ["select/Northaven"]
