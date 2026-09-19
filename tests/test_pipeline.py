"""The four steps together, against scripted responses."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from port_tariff_agent.errors import ClassificationError, LlmError, TranscriptionError
from port_tariff_agent.ingestion.classifier import ChargeClassification
from port_tariff_agent.ingestion.pipeline import ingest_document
from port_tariff_agent.ingestion.profiler import RawDocumentProfile
from port_tariff_agent.ingestion.transcriber import PageTranscription
from port_tariff_agent.models import ChargesFile, Payer, TariffIndexFile
from port_tariff_agent.registry import load_registry
from port_tariff_agent.settings import Settings
from port_tariff_agent.storage import read_json
from tests.fakes import FakeLlm

pytestmark = pytest.mark.integration

PAGE_BODIES = {
    1: """<!-- printed-page: 1 -->

A schedule of what is payable at the sites listed here.

# 1 SITE SERVICES

General terms for this section.

## 1.1 WIDGET HANDLING

| Band | Alpha Bay | Other Sites |
|---|---|---|
| Up to 10 000 units | 1 234.56 | 987.65 |
""",
    2: """<!-- printed-page: 2 -->

## 1.2 SPROCKET LEVY

Payable per visit at Bravo Point.
""",
    3: """<!-- printed-page: 3 -->

# 2 ADMINISTRATION

Forms and procedure, nothing payable.
""",
}

PROFILE = RawDocumentProfile(
    issuer="Some Authority",
    title="Tariff Book, Third Edition",
    currency="ZAR",
    valid_from="2024-04-01",
    valid_to="2025-03-31",
)


def responder(request):
    call_id = request.call_id
    if call_id.startswith("transcribe/"):
        return PageTranscription(markdown=PAGE_BODIES[int(call_id.split("_")[-1])])
    if call_id == "profile/document":
        return PROFILE
    if call_id == "classify/1.1":
        return ChargeClassification(
            defines_charge=True,
            charge_name="Widget Handling",
            payer=Payer.VESSEL,
            applies_when="Whenever widgets are handled",
            ports_mentioned=["Alpha Bay"],
        )
    if call_id == "classify/1.2":
        return ChargeClassification(
            defines_charge=True,
            charge_name="Sprocket Levy",
            payer=Payer.VESSEL,
            applies_when="On every visit",
            ports_mentioned=["Bravo Point"],
        )
    return ChargeClassification(defines_charge=False)


@pytest.fixture
def pdf(tiny_pdf: Callable[[int], Path]) -> Path:
    return tiny_pdf(3)


def run(pdf: Path, settings: Settings, llm: FakeLlm, **kwargs: object):
    return ingest_document(pdf, settings=settings, client=llm, **kwargs)


def test_every_artifact_is_written(pdf: Path, settings: Settings) -> None:
    result = run(pdf, settings, FakeLlm(default=responder))
    paths = result.paths

    assert paths.source_pdf.exists()
    assert paths.tariff_md.exists()
    assert paths.index_json.exists()
    assert paths.charges_json.exists()
    assert paths.manifest_json.exists()
    assert paths.classifications_jsonl.exists()
    assert paths.profile_json.exists()


def test_the_registry_row_describes_the_document(pdf: Path, settings: Settings) -> None:
    result = run(pdf, settings, FakeLlm(default=responder))
    row = result.row

    assert row.issuer == "Some Authority"
    assert row.currency == "ZAR"
    assert row.valid_from is not None and row.valid_from.isoformat() == "2024-04-01"
    assert row.ports == ["Alpha Bay", "Bravo Point"]
    assert row.page_count == 3
    assert row.source == pdf.name
    assert load_registry(settings.data_dir)[0].document_hash == result.document_hash


def test_the_index_and_catalog_match_the_document(pdf: Path, settings: Settings) -> None:
    result = run(pdf, settings, FakeLlm(default=responder))

    index = TariffIndexFile.model_validate(read_json(result.paths.index_json))
    assert [node.id for node in index.sections] == ["front-matter", "1", "1.1", "1.2", "2"]

    charges = ChargesFile.model_validate(read_json(result.paths.charges_json))
    assert [charge.section_id for charge in charges.charges] == ["1.1", "1.2"]
    assert charges.document_hash == result.document_hash


def test_printed_pages_are_tracked_across_pdf_pages(pdf: Path, settings: Settings) -> None:
    result = run(pdf, settings, FakeLlm(default=responder))
    index = TariffIndexFile.model_validate(read_json(result.paths.index_json))
    by_id = {node.id: node for node in index.sections}
    assert by_id["1.1"].printed_page == 1
    assert by_id["1.2"].printed_page == 2
    assert by_id["2"].pdf_page == 3


def test_a_second_run_is_skipped_entirely(pdf: Path, settings: Settings) -> None:
    run(pdf, settings, FakeLlm(default=responder))

    second = FakeLlm(default=responder)
    result = run(pdf, settings, second)
    assert result.already_ingested
    assert second.call_count == 0
    assert len(load_registry(settings.data_dir)) == 1


def test_force_re_runs_every_step_and_keeps_one_row(pdf: Path, settings: Settings) -> None:
    run(pdf, settings, FakeLlm(default=responder))

    second = FakeLlm(default=responder)
    result = run(pdf, settings, second, force=True)
    assert not result.already_ingested
    assert second.call_count > 0
    assert len(load_registry(settings.data_dir)) == 1


def test_a_failed_page_stops_the_run_before_the_registry(pdf: Path, settings: Settings) -> None:
    def broken(request):
        if request.call_id == "transcribe/page_002":
            raise LlmError("page blew up", retryable=False)
        return responder(request)

    with pytest.raises(TranscriptionError) as exc:
        run(pdf, settings, FakeLlm(default=broken))

    assert exc.value.failed_pages == [2]
    assert load_registry(settings.data_dir) == []


def test_a_failed_section_stops_the_run_before_the_registry(pdf: Path, settings: Settings) -> None:
    def broken(request):
        if request.call_id == "classify/1.1":
            raise LlmError("classifier blew up", retryable=False)
        return responder(request)

    with pytest.raises(ClassificationError) as exc:
        run(pdf, settings, FakeLlm(default=broken))

    assert exc.value.section_ids == ["1.1"]
    assert load_registry(settings.data_dir) == []


def test_the_run_resumes_from_what_survived(pdf: Path, settings: Settings) -> None:
    def broken(request):
        if request.call_id == "classify/1.1":
            raise LlmError("classifier blew up", retryable=False)
        return responder(request)

    with pytest.raises(ClassificationError):
        run(pdf, settings, FakeLlm(default=broken))

    second = FakeLlm(default=responder)
    result = run(pdf, settings, second)

    assert not result.already_ingested
    assert not any(call.startswith("transcribe/") for call in second.call_ids)
    assert load_registry(settings.data_dir)[0].document_hash == result.document_hash


def test_the_summary_counts_what_happened(pdf: Path, settings: Settings) -> None:
    result = run(pdf, settings, FakeLlm(default=responder))
    assert result.page_count == 3
    assert result.section_count == 5
    assert result.charge_count == 2
    assert result.classified + result.skipped == result.section_count


def test_a_clean_document_raises_no_errors(pdf: Path, settings: Settings) -> None:
    result = run(pdf, settings, FakeLlm(default=responder))
    assert [finding.code for finding in result.findings if finding.level == "error"] == []


def test_a_changed_prompt_reopens_an_already_ingested_document(
    pdf: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(pdf, settings, FakeLlm(default=responder))

    from port_tariff_agent.ingestion import pipeline
    from port_tariff_agent.prompts import CHARGE_CLASSIFICATION, Prompt

    revised = Prompt(
        name=CHARGE_CLASSIFICATION.name,
        version=CHARGE_CLASSIFICATION.version + 1,
        text=CHARGE_CLASSIFICATION.text + "\nAn extra instruction.\n",
    )
    monkeypatch.setattr(pipeline, "CHARGE_CLASSIFICATION", revised)

    second = FakeLlm(default=responder)
    result = run(pdf, settings, second)

    assert not result.already_ingested
    assert not any(call.startswith("transcribe/") for call in second.call_ids)
    assert any(call.startswith("classify/") for call in second.call_ids)
    assert len(load_registry(settings.data_dir)) == 1
