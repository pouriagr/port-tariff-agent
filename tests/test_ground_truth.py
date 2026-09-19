"""The reference case: does the agent price a real vessel call correctly (ADR-023)?

The expected values and their derivation are in `docs/spec/ground-truth.md`. They are
reference figures supplied with the task, not this project's output, and two of them
deviate slightly from what the document's own formulas give; the tolerance exists for
those two and is bounded separately below.

By default this replays a recorded conversation against the committed artifacts, so it
runs in CI with no key and no network. `--live` re-records it first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from port_tariff_agent.agent.answer import TariffAnswer
from port_tariff_agent.agent.factory import build_agent
from port_tariff_agent.llm.client import GeminiClient
from port_tariff_agent.models import DocumentRow
from port_tariff_agent.paths import DocumentPaths
from port_tariff_agent.registry import load_registry, select_document
from port_tariff_agent.settings import Settings, load_settings
from port_tariff_agent.tariff_index import TariffIndex
from tests.cassettes import Cassette, RecordingClient, ReplayingClient
from tests.provenance import check_columns, check_provenance, differing_sections
from tests.report import (
    ACCURACY_END,
    ACCURACY_START,
    GENERALITY_END,
    GENERALITY_START,
    extract_block,
    normalise,
    render_accuracy_block,
    render_generality_block,
    replacement_message,
)

pytestmark = pytest.mark.integration

# `clean_env` moves the working directory to a temporary one, so every path here is
# absolute and anchored on the repository rather than on the caller's cwd.
REPO = Path(__file__).resolve().parents[1]
DATA_DIR = REPO / "data"

DURBAN = "Durban"
DURBAN_CASSETTE = "durban_reference"

CAPE_TOWN = "Cape Town"
CAPE_TOWN_CASSETTE = "cape_town_generality"

ARRIVAL = date(2024, 11, 15)
TODAY = date(2026, 9, 19)

TOLERANCE = 0.005
"""0.5 percent, per the spec: two reference figures are themselves off by about 0.09."""

NEAR_EXACT = 0.0005
"""What the other four reproduce to. Anything looser there would be a regression."""

KNOWN_DEVIATIONS = {"2.1.1", "4.1.1"}


@dataclass(frozen=True, slots=True)
class Reference:
    section_id: str
    label: str
    expected: float


REFERENCE = (
    Reference("1.1.1", "Light Dues", 60_062.04),
    Reference("2.1.1", "VTS Dues", 33_315.75),
    Reference("3.3", "Pilotage Dues", 47_189.94),
    Reference("3.6", "Towage Dues", 147_074.38),
    Reference("3.8", "Running Lines", 19_639.50),
    Reference("4.1.1", "Port Dues", 199_549.22),
)


def reference_query(port: str) -> str:
    """The vessel call, stated once so another port is provably the same question.

    It says what the vessel is and what it did. It says nothing about which sections to
    read, which column to use, how many services a call involves or how to round: working
    that out from the document is what is being tested.
    """
    return (
        f"What port charges does the vessel SUDESTADA have to pay for her call at {port}?\n"
        "Vessel: SUDESTADA, bulk carrier, built 2010, flag Malta.\n"
        "Gross tonnage 51,300. Net tonnage 31,192. DWT 93,274."
        " Length overall 229.2 m. Beam 38.0 m.\n"
        "Arrived 2024-11-15 10:12, departed 2024-11-22 13:00, 3.39 days alongside.\n"
        "Activity: exporting iron ore, 40,000 t, 2 cargo operations, 7 holds.\n"
        "List every charge she owes, with the section and page each rate came from."
    )


@dataclass(frozen=True, slots=True)
class Run:
    """One replayed conversation and everything needed to check where its numbers came from."""

    port: str
    cassette: Cassette
    answer: TariffAnswer
    index: TariffIndex
    row: DocumentRow

    def context_for(self, section_id: str) -> str:
        return self.index.get_context(section_id)


def resolve_document(port: str) -> DocumentRow:
    row = select_document(load_registry(DATA_DIR), port=port, on=ARRIVAL)
    if row is None:
        raise AssertionError(
            f"no committed document covers {port} on {ARRIVAL}."
            f" Run `uv run port-tariff ingest 'data/raw/Port Tariff.pdf'`."
        )
    return row


def load_index(row: DocumentRow) -> TariffIndex:
    return TariffIndex.load(DocumentPaths(DATA_DIR, row.document_hash).index_json)


def settings_for(models: Mapping[str, str], *, api_key: str = "replayed") -> Settings:
    return Settings(
        gemini_api_key=api_key,  # type: ignore[arg-type]
        gemini_model_agent=models["agent"],
        gemini_model_extract=models["extract"],
        gemini_model_classify=models.get("classify", models["extract"]),
        data_dir=DATA_DIR,
        llm_base_delay_s=0,
    )


def replay_run(name: str, port: str) -> Run:
    """Replay a tape, having first checked it still describes this question and document."""
    cassette = Cassette.load(name)
    row = resolve_document(port)

    assert cassette.query == reference_query(port), (
        f"cassette {name!r} recorded a different question; the taped turns answer that one."
        " Re-record with --live."
    )
    assert cassette.document_hash == row.document_hash, (
        f"cassette {name!r} was recorded against document {cassette.document_hash[:12]},"
        f" but {port} now resolves to {row.document_hash[:12]}. Re-record with --live."
    )

    client = ReplayingClient(cassette)
    agent = build_agent(settings_for(cassette.models), client=client, today=cassette.today)
    answer = agent.ask(cassette.query)
    client.assert_exhausted()

    return Run(port=port, cassette=cassette, answer=answer, index=load_index(row), row=row)


def record_run(name: str, port: str) -> Run:
    """Ask the real API and write the tape. Only ever reached under `--live`."""
    settings = load_settings()
    row = resolve_document(port)
    query = reference_query(port)
    models = {
        "agent": settings.gemini_model_agent,
        "extract": settings.gemini_model_extract,
    }
    recorder = RecordingClient(
        GeminiClient(settings),
        name=name,
        query=query,
        today=TODAY,
        document_hash=row.document_hash,
        models=models,
    )
    agent = build_agent(settings_for(models, api_key="recorded"), client=recorder, today=TODAY)
    answer = agent.ask(query)
    cassette = recorder.finish()
    print(f"\nrecorded {cassette.save()}")

    return Run(port=port, cassette=cassette, answer=answer, index=load_index(row), row=row)


def amounts_by_section(answer: TariffAnswer) -> dict[str, float]:
    """Total per section. A call can be billed as two lines citing one section."""
    totals: dict[str, float] = {}
    for line in answer.charges:
        totals[line.section_id] = totals.get(line.section_id, 0.0) + line.amount
    return totals


def deviation(computed: float, expected: float) -> float:
    return (computed - expected) / expected


def assert_reference_values(run: Run) -> None:
    """The whole claim in one place, so a recording is never saved in a failing state."""
    amounts = amounts_by_section(run.answer)
    missing = [ref.section_id for ref in REFERENCE if ref.section_id not in amounts]
    assert not missing, f"the answer priced {sorted(amounts)}; it is missing {missing}"
    for ref in REFERENCE:
        assert amounts[ref.section_id] == pytest.approx(ref.expected, rel=TOLERANCE), (
            f"{ref.label} ({ref.section_id}): {amounts[ref.section_id]:,.2f}"
            f" against {ref.expected:,.2f}"
        )


# --------------------------------------------------------------------------------------
# Recording. Skipped unless --live; see tests/conftest.py.
# --------------------------------------------------------------------------------------


@pytest.mark.live
def test_record_the_durban_reference_run() -> None:
    """Re-record the reference tape: `uv run pytest -k record --live`.

    It asserts the same values the replay does, so a bad recording fails here rather than
    being committed and discovered later.
    """
    assert_reference_values(record_run(DURBAN_CASSETTE, DURBAN))


@pytest.mark.live
def test_record_the_cape_town_generality_run() -> None:
    """The same question at a second port. No expected values exist, so the recording
    only has to be traceable: every rate from the section it cites, and from a column
    that belongs to this port or to no port."""
    run = record_run(CAPE_TOWN_CASSETTE, CAPE_TOWN)

    assert check_provenance(run) == []
    assert check_columns(run) == []


# --------------------------------------------------------------------------------------
# Replay. This is what CI runs.
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def durban_run() -> Run:
    return replay_run(DURBAN_CASSETTE, DURBAN)


@pytest.mark.parametrize("ref", REFERENCE, ids=lambda ref: ref.section_id)
def test_each_reference_charge_is_within_tolerance(durban_run: Run, ref: Reference) -> None:
    amounts = amounts_by_section(durban_run.answer)

    assert ref.section_id in amounts, (
        f"{ref.label} ({ref.section_id}) is missing; the answer priced {sorted(amounts)}"
    )
    assert amounts[ref.section_id] == pytest.approx(ref.expected, rel=TOLERANCE)


def test_all_six_reference_sections_are_priced(durban_run: Run) -> None:
    assert {ref.section_id for ref in REFERENCE} <= set(amounts_by_section(durban_run.answer))


def test_only_the_two_documented_figures_deviate(durban_run: Run) -> None:
    """The tolerance is 0.5 percent for two known reference-side errors.

    The other four reproduce to the cent. Without this, a real regression could hide
    inside the slack the tolerance leaves.
    """
    amounts = amounts_by_section(durban_run.answer)
    drifting = {
        ref.section_id
        for ref in REFERENCE
        if abs(deviation(amounts[ref.section_id], ref.expected)) > NEAR_EXACT
    }

    assert drifting == KNOWN_DEVIATIONS


def test_extra_charges_resolve_in_the_document(durban_run: Run) -> None:
    """Pricing more than the reference table is allowed; inventing a section is not."""
    extras = sorted(set(amounts_by_section(durban_run.answer)) - {r.section_id for r in REFERENCE})
    print(f"\ncharges beyond the reference table: {extras or 'none'}")

    unknown = [sid for sid in extras if durban_run.index.get_node(sid) is None]
    assert unknown == [], f"the answer cites sections the document does not have: {unknown}"


def test_the_total_is_the_sum_of_the_lines(durban_run: Run) -> None:
    """Catches arithmetic done in prose rather than through the calculate tool."""
    lines = sum(line.amount for line in durban_run.answer.charges)

    assert durban_run.answer.total == pytest.approx(lines, abs=0.05)


def test_the_answer_reports_the_document_s_currency(durban_run: Run) -> None:
    assert durban_run.answer.currency == durban_run.row.currency


def test_nothing_was_reported_missing(durban_run: Run) -> None:
    """The query supplies every input the reference charges need."""
    assert durban_run.answer.missing_inputs == []


def test_every_charge_cites_a_section_and_a_page(durban_run: Run) -> None:
    for line in durban_run.answer.charges:
        assert durban_run.index.get_node(line.section_id) is not None, line.section_id
        assert line.page_citation == durban_run.index.page_citation(line.section_id)


def test_the_readme_accuracy_block_matches_the_recorded_run(durban_run: Run) -> None:
    """The README's numbers are the recorded run's numbers, or this fails with the fix."""
    rendered = render_accuracy_block(durban_run, REFERENCE)
    current = extract_block(
        (REPO / "README.md").read_text(encoding="utf-8"), ACCURACY_START, ACCURACY_END
    )

    assert normalise(current) == normalise(rendered), replacement_message("accuracy", rendered)


# --------------------------------------------------------------------------------------
# Generality: the same question at a second port, with no code changed (ADR-024).
#
# There are no reference values for Cape Town, so nothing here asserts an amount. What is
# asserted is where the amounts came from.
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cape_town_run() -> Run:
    return replay_run(CAPE_TOWN_CASSETTE, CAPE_TOWN)


def test_the_same_question_finds_the_same_charges_at_another_port(
    durban_run: Run, cape_town_run: Run
) -> None:
    assert set(amounts_by_section(cape_town_run.answer)) == set(
        amounts_by_section(durban_run.answer)
    )


def test_the_two_ports_did_not_resolve_to_the_same_constants(
    durban_run: Run, cape_town_run: Run
) -> None:
    """If every rate matched, the column was not being chosen from the document at all."""
    differing = differing_sections(durban_run, cape_town_run)

    print(f"\nsections whose rates differ between the two ports: {sorted(differing)}")
    assert differing, "both ports used identical constants everywhere"


@pytest.mark.parametrize("name", ["durban_run", "cape_town_run"])
def test_every_rate_came_from_the_section_it_is_cited_from(
    name: str, request: pytest.FixtureRequest
) -> None:
    run: Run = request.getfixturevalue(name)

    assert check_provenance(run) == []


@pytest.mark.parametrize("name", ["durban_run", "cape_town_run"])
def test_every_rate_came_from_this_port_s_column_or_the_fallback(
    name: str, request: pytest.FixtureRequest
) -> None:
    """The one check that catches a run reading the wrong port's column of the right table."""
    run: Run = request.getfixturevalue(name)

    assert check_columns(run) == []


def test_the_second_port_s_answer_stands_on_its_own(cape_town_run: Run) -> None:
    answer = cape_town_run.answer

    assert answer.missing_inputs == []
    assert answer.currency == cape_town_run.row.currency
    assert all(line.amount > 0 for line in answer.charges)
    assert answer.total == pytest.approx(sum(line.amount for line in answer.charges), abs=0.05)


def test_the_readme_generality_block_matches_the_recorded_runs(
    durban_run: Run, cape_town_run: Run
) -> None:
    rendered = render_generality_block(durban_run, cape_town_run)
    current = extract_block(
        (REPO / "README.md").read_text(encoding="utf-8"), GENERALITY_START, GENERALITY_END
    )

    assert normalise(current) == normalise(rendered), replacement_message("generality", rendered)
