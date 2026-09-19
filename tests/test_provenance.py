"""The generality checks, against invented ports and invented rates.

Nothing here touches the real tariff: a helper that only works on the TNPA book would not
be a generality check. The tables are written inline rather than as one shared fixture
file so each test can vary the one thing it is about.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from port_tariff_agent.agent.answer import ChargeLine, TariffAnswer
from port_tariff_agent.models import DocumentRow, SectionNode, TariffIndexFile
from port_tariff_agent.tariff_index import TariffIndex
from tests.cassettes import Cassette
from tests.provenance import (
    check_columns,
    check_provenance,
    differing_sections,
    formula_literals,
    normalise,
    parse_tables,
    rate_literals,
    stated_numbers,
)

HOME = "Northaven"
AWAY = "Westhaven"
UNLISTED = "Southbank"  # a port of the document with no column of its own
PORTS = [HOME, AWAY, "Eastmouth", UNLISTED]

PORT_TABLE = """The following fees are payable per service.

| | Northaven | Port Elizabeth / Eastmouth | Westhaven | Other Ports |
| --- | --- | --- | --- | --- |
| Basic fee | 1 200.50 | 1 450.75 | 1 310.60 | 900.25 |
| Plus per 100 tons or part thereof | 5.10 | 6.20 | 7.30 | 4.30 |
"""

NO_PORT_TABLE = """A flat charge applies to every vessel.

| Class | Rate per 100 tons |
| --- | --- |
| Deep sea | 42.75 |
"""

ROW_ORIENTED = """Rates by port.

| Port | Rate |
| --- | --- |
| Northaven | 88.88 |
| Westhaven | 99.99 |
"""

QUERY = "A vessel of 51,300 tons staying 3.39 days at " + HOME


class TestNormalise:
    @pytest.mark.parametrize(
        ("printed", "joined"),
        [
            ("2 801.91", "2801.91"),
            ("73 118.07", "73118.07"),
            ("51,300", "51300"),
            ("1 234 567.89", "1234567.89"),
        ],
    )
    def test_a_separator_between_digits_is_removed(self, printed: str, joined: str) -> None:
        assert normalise(printed) == joined

    def test_a_space_that_is_not_between_digits_survives(self) -> None:
        assert normalise("Basic fee 900.25 per call") == "Basic fee 900.25 per call"


class TestRateLiterals:
    def test_two_decimals_are_a_rate(self) -> None:
        assert rate_literals("the fee is 1 200.50 per service") == {"1200.50"}

    def test_structural_numbers_are_not_rates(self) -> None:
        """`per 100 tons`, two services, a band floor: none of these are quoted rates."""
        assert rate_literals("2 * (x + ceil(51300 / 100) - 50000)") == set()

    def test_one_decimal_is_not_a_rate(self) -> None:
        assert rate_literals("length 229.2 m") == set()

    def test_more_than_two_decimals_is_not_a_rate(self) -> None:
        assert rate_literals("factor 1.2345") == set()

    def test_several_rates_come_back_together(self) -> None:
        assert rate_literals("| 1 200.50 | 5.10 |") == {"1200.50", "5.10"}


class TestStatedNumbers:
    def test_the_vessel_s_own_figures_are_collected(self) -> None:
        assert {"51300", "3.39"} <= stated_numbers(QUERY)

    def test_they_are_what_keeps_them_out_of_the_rate_check(self) -> None:
        """3.39 is rate-shaped, so only the query can tell us it is not a rate."""
        assert "3.39" in rate_literals("3.39")
        assert "3.39" in stated_numbers(QUERY)


class TestParseTables:
    def test_headers_and_their_cells_line_up(self) -> None:
        (table,) = parse_tables(PORT_TABLE)

        headers = [column.header for column in table.columns]
        assert headers == ["", HOME, "Port Elizabeth / Eastmouth", AWAY, "Other Ports"]
        assert table.columns[1].cells == ("1 200.50", "5.10")
        assert table.columns[4].cells == ("900.25", "4.30")

    def test_a_cell_is_found_however_it_was_printed(self) -> None:
        (table,) = parse_tables(PORT_TABLE)

        assert table.holds("1200.50")
        assert not table.holds("1200.51")

    def test_prose_between_tables_separates_them(self) -> None:
        assert len(parse_tables(PORT_TABLE + "\nAnd also:\n\n" + NO_PORT_TABLE)) == 2

    def test_text_without_a_table_yields_none(self) -> None:
        assert parse_tables("Pilotage is compulsory at every port.") == []

    def test_a_combined_header_names_both_of_its_ports(self) -> None:
        (table,) = parse_tables(PORT_TABLE)
        combined = table.columns[2]

        assert combined.names_one_of(["Eastmouth"])
        assert combined.names_one_of(["Port Elizabeth"])
        assert not combined.names_one_of([HOME])

    def test_a_header_that_is_not_a_port_names_none(self) -> None:
        (table,) = parse_tables(PORT_TABLE)

        assert not table.columns[4].names_one_of(PORTS)  # "Other Ports"
        assert not table.columns[0].names_one_of(PORTS)  # the label column


def build_run(
    *,
    port: str = HOME,
    text: str = PORT_TABLE,
    formula: str = "2 * (1200.50 + ceil(51300 / 100) * 5.10)",
    section_id: str = "1.1",
    query: str = QUERY,
) -> Any:
    """A Run over one invented section, which is all these helpers read."""
    from tests.test_ground_truth import Run

    index = TariffIndex(
        TariffIndexFile(
            document_hash="0f1e2d3c4b5a",
            sections=[
                SectionNode(
                    id=section_id,
                    title="A Charge",
                    parent=None,
                    children=[],
                    order=0,
                    pdf_page=1,
                    printed_page=1,
                    text=text,
                )
            ],
        )
    )
    answer = TariffAnswer(
        answer="one charge",
        charges=[
            ChargeLine(
                name="A Charge",
                section_id=section_id,
                page_citation="1",
                formula=formula,
                amount=1.0,
            )
        ],
    )
    return Run(
        port=port,
        cassette=_cassette(query),
        answer=answer,
        index=index,
        row=DocumentRow(
            document_hash="0f1e2d3c4b5a",
            source="synthetic.pdf",
            ports=PORTS,
            page_count=1,
            ingested_at="2026-01-01T00:00:00Z",
        ),
    )


def _cassette(query: str) -> Cassette:
    return Cassette(
        name="synthetic",
        recorded_at="2026-01-01T00:00:00Z",
        document_hash="0f1e2d3c4b5a",
        query=query,
        today=date(2026, 1, 1),
        models={"agent": "model-agent", "extract": "model-extract"},
        prompts={},
        interactions=(),
    )


class TestProvenance:
    def test_a_rate_in_the_cited_section_is_accepted(self) -> None:
        assert check_provenance(build_run()) == []

    def test_a_rate_that_is_nowhere_in_the_section_is_reported(self) -> None:
        run = build_run(formula="ceil(51300 / 100) * 999.99")

        (problem,) = check_provenance(run)
        assert "999.99 is not in that section's text" in problem

    def test_the_question_s_own_numbers_are_not_looked_for(self) -> None:
        """3.39 is rate-shaped but came from the user, not from the document."""
        run = build_run(formula="1200.50 * 3.39")

        assert check_provenance(run) == []

    def test_a_rate_printed_with_a_space_still_matches(self) -> None:
        assert check_provenance(build_run(formula="1 200.50 * 2")) == []

    def test_only_the_named_sections_are_checked(self) -> None:
        run = build_run(formula="ceil(51300 / 100) * 999.99")

        assert check_provenance(run, only=["9.9"]) == []
        assert check_provenance(run, only=["1.1"]) != []


class TestColumns:
    def test_this_port_s_own_column_is_accepted(self) -> None:
        assert check_columns(build_run(formula="1200.50 + 5.10")) == []

    def test_another_port_s_column_is_reported(self) -> None:
        run = build_run(formula="1310.60 + 7.30")

        problems = check_columns(run)
        assert problems and "but the table has a 'Northaven' column" in problems[0]

    def test_a_port_without_a_column_must_use_the_fallback(self) -> None:
        assert check_columns(build_run(port=UNLISTED, formula="900.25 + 4.30")) == []

    def test_a_port_without_a_column_may_not_borrow_another_s(self) -> None:
        run = build_run(port=UNLISTED, formula="1200.50 + 5.10")

        problems = check_columns(run)
        assert problems and "should come from a column naming no port" in problems[0]

    def test_a_combined_header_counts_as_that_port_s_own_column(self) -> None:
        assert check_columns(build_run(port="Eastmouth", formula="1450.75 + 6.20")) == []

    def test_a_table_with_no_port_columns_is_left_alone(self) -> None:
        assert check_columns(build_run(text=NO_PORT_TABLE, formula="42.75 * 2")) == []

    def test_a_row_oriented_table_degrades_rather_than_failing_wrongly(self) -> None:
        """Ports down the first column are not recognised; the check says nothing."""
        assert check_columns(build_run(text=ROW_ORIENTED, formula="99.99 * 2")) == []

    def test_a_rate_that_is_in_no_table_is_left_to_the_provenance_check(self) -> None:
        assert check_columns(build_run(formula="777.77 * 2")) == []


class TestDifferingSections:
    def test_two_ports_reading_different_columns_differ(self) -> None:
        home = build_run(formula="1200.50 + 5.10")
        away = build_run(port=AWAY, formula="1310.60 + 7.30")

        assert differing_sections(home, away) == {"1.1"}

    def test_two_ports_reading_the_same_rate_do_not(self) -> None:
        home = build_run(text=NO_PORT_TABLE, formula="42.75 * 2")
        away = build_run(port=AWAY, text=NO_PORT_TABLE, formula="42.75 * 2")

        assert differing_sections(home, away) == set()

    def test_a_section_only_one_port_priced_is_not_compared(self) -> None:
        home = build_run(section_id="1.1")
        away = build_run(port=AWAY, section_id="2.2", formula="1310.60 + 7.30")

        assert differing_sections(home, away) == set()


def test_the_literals_of_a_section_exclude_the_question_s_numbers() -> None:
    run = build_run(formula="2 * (1200.50 + ceil(51300 / 100) * 5.10) * 3.39")

    assert formula_literals(run, "1.1") == {"1200.50", "5.10"}
