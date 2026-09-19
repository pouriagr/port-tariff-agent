"""What `get_charges` hands the agent: the right document, and the text it has to read."""

from __future__ import annotations

from datetime import date

from port_tariff_agent.agent.knowledge import Document as LoadedDocument
from port_tariff_agent.agent.knowledge import get_charges
from port_tariff_agent.agent.selector import ChargeSelector, Selection, SelectorResponse
from tests.conftest import Document
from tests.fakes import FakeLlm

IN_VALIDITY = date(2024, 6, 1)


def selector(response: SelectorResponse) -> ChargeSelector:
    return ChargeSelector(FakeLlm(default=response), model="model-extract")


def fetch(document: Document, response: SelectorResponse, **overrides: object) -> dict:
    kwargs = {
        "data_dir": document.data_dir,
        "selector": selector(response),
        "port": document.port,
        "vessel_description": "A vessel",
        "arrival_date": IN_VALIDITY,
    }
    kwargs.update(overrides)
    return get_charges(**kwargs)  # type: ignore[arg-type]


def test_an_unknown_port_is_an_error_that_names_the_ports_it_knows(document: Document) -> None:
    result = fetch(document, SelectorResponse(), port="Southhaven")
    assert "error" in result
    assert result["known_ports"] == [document.port]


def test_a_date_outside_the_validity_finds_nothing(document: Document) -> None:
    result = fetch(document, SelectorResponse(), arrival_date=date(2030, 1, 1))
    assert "error" in result
    assert "2030-01-01" in result["error"]


def test_an_empty_registry_is_an_error_not_a_crash(tmp_path: object) -> None:
    result = get_charges(
        data_dir=tmp_path / "nowhere",  # type: ignore[operator]
        selector=selector(SelectorResponse()),
        port="Northaven",
        vessel_description="A vessel",
        arrival_date=IN_VALIDITY,
    )
    assert result["known_ports"] == []


def test_it_returns_the_document_the_agent_has_to_cite(document: Document) -> None:
    result = fetch(document, SelectorResponse())
    assert result["document"]["title"] == "Synthetic Tariff"
    assert result["document"]["currency"] == "XTS"
    assert result["document"]["valid_from"] == "2024-01-01"
    assert result["port"] == document.port


def test_an_applicable_charge_carries_its_name_page_reason_and_text(document: Document) -> None:
    result = fetch(
        document,
        SelectorResponse(applicable=[Selection(section_id="1.2", reason="it arrives")]),
    )
    (charge,) = result["applicable"]
    assert charge["name"] == "Arrival Fee"
    assert charge["page_citation"] == "4"
    assert charge["reason"] == "it arrives"
    assert "per 100 tons or part thereof" in charge["text"]


def test_the_text_carries_what_the_parent_section_says(document: Document) -> None:
    result = fetch(document, SelectorResponse(applicable=[Selection(section_id="1.2", reason="x")]))
    assert "payable by the vessel" in result["applicable"][0]["text"]


def test_a_context_section_comes_back_with_its_terms(document: Document) -> None:
    result = fetch(
        document,
        SelectorResponse(context_sections=[Selection(section_id="1.1", reason="working hours")]),
    )
    (context,) = result["context"]
    assert context["section_id"] == "1.1"
    assert context["name"] == "GENERAL"
    assert "Working hours" in context["text"]


def test_what_was_not_selected_is_reported_without_a_reason(document: Document) -> None:
    result = fetch(document, SelectorResponse(applicable=[Selection(section_id="1.2", reason="x")]))
    assert result["not_applicable"] == [{"section_id": "1.3", "name": "Mooring Fee"}]


def test_the_selector_is_offered_the_sections_that_define_no_charge(document: Document) -> None:
    loaded = LoadedDocument.load(document.data_dir, document.row)
    offered = [node.id for node in loaded.context_candidates()]
    assert "1.1" in offered
    assert "1.2" not in offered


def test_a_container_with_no_text_is_not_offered_as_context(document: Document) -> None:
    loaded = LoadedDocument.load(document.data_dir, document.row)
    assert all(node.text.strip() for node in loaded.context_candidates())
