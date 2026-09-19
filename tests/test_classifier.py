"""Step 3: the skip rule, the catalog, and who owns the section id."""

from __future__ import annotations

from pathlib import Path

import pytest

from port_tariff_agent.errors import LlmError
from port_tariff_agent.ingestion.classifier import (
    ChargeClassification,
    ChargeClassifier,
    content_text,
    should_classify,
)
from port_tariff_agent.ingestion.index_builder import build_index
from port_tariff_agent.models import ChargesFile, Payer, SectionNode
from port_tariff_agent.paths import DocumentPaths
from port_tariff_agent.storage import read_json
from port_tariff_agent.tariff_index import TariffIndex
from tests.fakes import FakeLlm

MARKDOWN = """# 1 GROUP

## 1.1 A CHARGE

| Band | Rate |
|---|---|
| Up to 100 | 12.34 |

### 1.1.1 SHORT LEAF

9.99

# 2 EMPTY CONTAINER
## 2.1 A LEAF UNDER IT

Something payable per visit.
"""


def node(**kwargs: object) -> SectionNode:
    payload: dict[str, object] = {"id": "1", "title": "T", "order": 0, "text": ""}
    payload.update(kwargs)
    return SectionNode.model_validate(payload)


@pytest.fixture
def index() -> TariffIndex:
    file, _ = build_index(MARKDOWN, document_hash="h")
    return TariffIndex(file)


@pytest.fixture
def paths(tmp_path: Path) -> DocumentPaths:
    target = DocumentPaths(data_dir=tmp_path / "data", document_hash="h")
    target.ensure()
    return target


def positive(name: str = "Some Charge", ports: list[str] | None = None) -> ChargeClassification:
    return ChargeClassification(
        defines_charge=True,
        charge_name=name,
        payer=Payer.VESSEL,
        applies_when="Whenever the service is rendered",
        ports_mentioned=ports or [],
    )


NEGATIVE = ChargeClassification(defines_charge=False)


def test_a_childless_section_is_classified_however_short() -> None:
    assert should_classify(node(id="1.1.1", text="9.99"))
    assert should_classify(node(id="1.1.1", text=""))


def test_a_section_that_is_mostly_a_table_is_not_skipped() -> None:
    table = "| Band | Rate |\n|---|---|\n| Up to 100 | 12.34 |"
    assert should_classify(node(id="1.1", children=["1.1.1"], text=table))


def test_an_empty_container_is_skipped() -> None:
    assert not should_classify(node(id="2", children=["2.1"], text="\n\n"))
    assert not should_classify(node(id="2", children=["2.1"], text="<!-- pdf-page: 001 -->"))


def test_content_text_ignores_rules_and_markers() -> None:
    assert content_text("|---|---|\n<!-- pdf-page: 001 -->\n\n") == ""
    assert content_text("real words") == "real words"


def test_skipped_sections_are_reported(index: TariffIndex, paths: DocumentPaths) -> None:
    llm = FakeLlm(default=NEGATIVE)
    report = ChargeClassifier(llm, model="m").run(index, paths)
    assert "2" in report.skipped
    assert "classify/2" not in llm.call_ids


def test_every_leaf_reaches_the_model(index: TariffIndex, paths: DocumentPaths) -> None:
    llm = FakeLlm(default=NEGATIVE)
    ChargeClassifier(llm, model="m").run(index, paths)
    assert {"classify/1.1.1", "classify/2.1"} <= set(llm.call_ids)


def test_only_positive_rows_reach_the_catalog(index: TariffIndex, paths: DocumentPaths) -> None:
    llm = FakeLlm({"classify/1.1": positive("Handling Fee")}, default=NEGATIVE)
    report = ChargeClassifier(llm, model="m").run(index, paths)

    assert [charge.section_id for charge in report.charges] == ["1.1"]
    stored = ChargesFile.model_validate(read_json(paths.charges_json))
    assert [charge.name for charge in stored.charges] == ["Handling Fee"]


def test_the_section_id_comes_from_the_loop_not_the_model(
    index: TariffIndex, paths: DocumentPaths
) -> None:
    assert "section_id" not in ChargeClassification.model_fields
    llm = FakeLlm({"classify/1.1": positive()}, default=NEGATIVE)
    report = ChargeClassifier(llm, model="m").run(index, paths)
    assert report.charges[0].section_id == "1.1"


def test_a_negative_answer_cannot_smuggle_fields_into_the_catalog() -> None:
    chatty = ChargeClassification(
        defines_charge=False, charge_name="Not A Charge", payer=Payer.VESSEL, applies_when="never"
    )
    assert chatty.charge_name is None
    assert chatty.payer is None
    assert chatty.applies_when is None


def test_a_positive_row_without_a_name_is_reported_not_stored(
    index: TariffIndex, paths: DocumentPaths
) -> None:
    nameless = ChargeClassification(defines_charge=True, charge_name=None)
    llm = FakeLlm({"classify/1.1": nameless}, default=NEGATIVE)
    report = ChargeClassifier(llm, model="m").run(index, paths)
    assert report.unnamed == ["1.1"]
    assert report.charges == []


def test_the_prompt_carries_the_heading_chain_and_the_section_text(
    index: TariffIndex, paths: DocumentPaths
) -> None:
    llm = FakeLlm(default=NEGATIVE)
    ChargeClassifier(llm, model="m").run(index, paths)
    prompt = llm.request_for("classify/1.1.1").prompt
    assert "1 GROUP > 1.1 A CHARGE > 1.1.1 SHORT LEAF" in prompt
    assert "9.99" in prompt


def test_ports_are_collected_from_positive_and_negative_sections(
    index: TariffIndex, paths: DocumentPaths
) -> None:
    llm = FakeLlm(
        {
            "classify/1.1": positive(ports=["Alpha Bay"]),
            "classify/2.1": ChargeClassification(
                defines_charge=False, ports_mentioned=["Bravo Point"]
            ),
        },
        default=NEGATIVE,
    )
    report = ChargeClassifier(llm, model="m").run(index, paths)
    assert set(report.ports) >= {"Alpha Bay", "Bravo Point"}


def test_cached_sections_are_not_classified_again(index: TariffIndex, paths: DocumentPaths) -> None:
    ChargeClassifier(FakeLlm(default=NEGATIVE), model="m").run(index, paths)

    second = FakeLlm(default=NEGATIVE)
    report = ChargeClassifier(second, model="m").run(index, paths)
    assert second.call_count == 0
    assert report.reused


def test_a_different_model_invalidates_the_cache(index: TariffIndex, paths: DocumentPaths) -> None:
    ChargeClassifier(FakeLlm(default=NEGATIVE), model="m").run(index, paths)

    second = FakeLlm(default=NEGATIVE)
    ChargeClassifier(second, model="other-model").run(index, paths)
    assert second.call_count > 0


def test_changed_section_text_invalidates_only_that_section(paths: DocumentPaths) -> None:
    first_file, _ = build_index(MARKDOWN, document_hash="h")
    ChargeClassifier(FakeLlm(default=NEGATIVE), model="m").run(TariffIndex(first_file), paths)

    edited_file, _ = build_index(MARKDOWN.replace("9.99", "10.99"), document_hash="h")
    second = FakeLlm(default=NEGATIVE)
    ChargeClassifier(second, model="m").run(TariffIndex(edited_file), paths)
    assert second.call_ids == ["classify/1.1.1"]


def test_a_failure_is_reported_and_blocks_the_catalog(
    index: TariffIndex, paths: DocumentPaths
) -> None:
    def responder(request):
        if request.call_id == "classify/1.1":
            raise LlmError("classifier blew up", retryable=False)
        return NEGATIVE

    report = ChargeClassifier(FakeLlm(default=responder), model="m").run(index, paths)
    assert report.failed == ["1.1"]
    assert not paths.charges_json.exists()


def test_charges_follow_document_order(index: TariffIndex, paths: DocumentPaths) -> None:
    llm = FakeLlm(
        {"classify/1.1": positive("First"), "classify/2.1": positive("Second")}, default=NEGATIVE
    )
    report = ChargeClassifier(llm, model="m").run(index, paths)
    assert [charge.name for charge in report.charges] == ["First", "Second"]
