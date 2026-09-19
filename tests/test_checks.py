"""Sanity checks: each one fires on the shape it is meant to catch."""

from __future__ import annotations

from port_tariff_agent.ingestion.checks import Level, run_sanity_checks
from port_tariff_agent.models import Charge, SectionNode


def node(section_id: str, **kwargs: object) -> SectionNode:
    payload: dict[str, object] = {
        "id": section_id,
        "title": "T",
        "order": 0,
        "pdf_page": 1,
        "printed_page": 1,
        "text": "Something payable, 12.34 per unit.",
    }
    payload.update(kwargs)
    return SectionNode.model_validate(payload)


CHARGE = [Charge(section_id="1", name="A Charge")]


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def test_a_clean_document_produces_nothing() -> None:
    sections = [node("1"), node("2", order=1)]
    charges = [Charge(section_id="1", name="A"), Charge(section_id="2", name="B")]
    assert run_sanity_checks(sections, charges, page_count=1) == []


def test_missing_page_markers_are_an_error() -> None:
    findings = run_sanity_checks([node("1", pdf_page=None)], CHARGE)
    assert "PAGE_MARKER_MISSING" in codes(findings)


def test_a_gap_between_page_markers_is_an_error() -> None:
    sections = [node("1", pdf_page=1), node("2", order=1, pdf_page=3)]
    findings = run_sanity_checks(sections, CHARGE)
    assert "PAGE_MARKER_GAP" in codes(findings)


def test_content_stopping_early_is_an_error() -> None:
    findings = run_sanity_checks([node("1", pdf_page=1)], CHARGE, page_count=27)
    assert "PAGE_MARKER_GAP" in codes(findings)


def test_too_many_unknown_printed_pages_is_a_warning() -> None:
    sections = [node(str(index), order=index, printed_page=None) for index in range(1, 5)]
    charges = [Charge(section_id="1", name="A")]
    findings = run_sanity_checks(sections, charges, page_count=1)
    assert "PRINTED_PAGE_UNKNOWN" in codes(findings)


def test_a_numbered_body_line_is_flagged_as_a_missed_heading() -> None:
    sections = [node("1", text="12.34 per unit\n2.1 SOMETHING THAT SHOULD BE A HEADING")]
    findings = run_sanity_checks(sections, CHARGE, page_count=1)
    assert "UNPARSED_HEADING" in codes(findings)


def test_a_table_row_is_not_mistaken_for_a_missed_heading() -> None:
    sections = [node("1", text="| 1.1 Band | 12.34 |")]
    findings = run_sanity_checks(sections, CHARGE, page_count=1)
    assert "UNPARSED_HEADING" not in codes(findings)


def test_an_empty_leaf_is_a_warning() -> None:
    sections = [node("1"), node("2", order=1, text="")]
    findings = run_sanity_checks(sections, CHARGE, page_count=1)
    assert "EMPTY_SECTION" in codes(findings)


def test_a_gap_in_sibling_numbering_is_a_warning() -> None:
    sections = [
        node("3", pdf_page=1),
        node("3.1", order=1, parent="3"),
        node("3.3", order=2, parent="3"),
    ]
    findings = run_sanity_checks(sections, [Charge(section_id="3.1", name="A")], page_count=1)
    assert "SIBLING_NUMBER_GAP" in codes(findings)


def test_a_ragged_table_is_a_warning() -> None:
    text = "| a | b |\n|---|---|\n| 1 | 2 | 3 |"
    findings = run_sanity_checks([node("1", text=text)], CHARGE, page_count=1)
    assert "RAGGED_TABLE" in codes(findings)


def test_an_empty_catalog_is_an_error() -> None:
    findings = run_sanity_checks([node("1")], [], page_count=1)
    assert "NO_CHARGES_FOUND" in codes(findings)


def test_a_numeric_leaf_that_defines_no_charge_is_a_warning() -> None:
    sections = [node("1"), node("2", order=1, text="9.99 per unit")]
    findings = run_sanity_checks(sections, CHARGE, page_count=1)
    assert "NUMERIC_LEAF_NOT_A_CHARGE" in codes(findings)


def test_levels_are_set_correctly() -> None:
    findings = run_sanity_checks([node("1")], [], page_count=1)
    assert all(finding.level in (Level.WARNING, Level.ERROR) for finding in findings)
    assert any(finding.level is Level.ERROR for finding in findings)
