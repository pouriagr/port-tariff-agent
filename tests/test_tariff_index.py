"""The read side: what the query phase will actually call."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from port_tariff_agent.ingestion.index_builder import build_index
from port_tariff_agent.models import SectionNode, TariffIndexFile
from port_tariff_agent.tariff_index import SectionNotFoundError, TariffIndex


@pytest.fixture
def index(load_markdown: Callable[[str], str]) -> TariffIndex:
    file, _ = build_index(load_markdown("simple"), document_hash="abc123")
    return TariffIndex(file)


def test_get_node_returns_none_for_an_unknown_id(index: TariffIndex) -> None:
    assert index.get_node("9.9") is None


def test_require_raises_for_an_unknown_id(index: TariffIndex) -> None:
    with pytest.raises(SectionNotFoundError):
        index.require("9.9")


def test_ancestors_are_root_first(index: TariffIndex) -> None:
    assert [node.id for node in index.ancestors("1.1.1")] == ["1", "1.1"]


def test_ancestors_follow_the_fallback_parent(index: TariffIndex) -> None:
    # 1.2 does not exist, so 1.2.3's only ancestor is 1.
    assert [node.id for node in index.ancestors("1.2.3")] == ["1"]


def test_descendants_are_in_document_order(index: TariffIndex) -> None:
    assert [node.id for node in index.descendants("1")] == ["1.1", "1.1.1", "1.2.3"]


def test_get_with_children_starts_with_the_section_itself(index: TariffIndex) -> None:
    text = index.get_with_children("1.1")
    assert text.startswith("## 1.1 WIDGET HANDLING")
    assert "### 1.1.1 WIDGET HANDLING, NIGHT RATE" in text


def test_get_with_children_of_a_leaf_is_just_that_leaf(index: TariffIndex) -> None:
    text = index.get_with_children("1.1.1")
    assert "WIDGET HANDLING, NIGHT RATE" in text
    assert "Minimum fee" not in text


def test_get_context_puts_ancestors_first(index: TariffIndex) -> None:
    text = index.get_context("1.1.1")
    positions = [text.index(marker) for marker in ("# 1 SITE SERVICES", "## 1.1", "### 1.1.1")]
    assert positions == sorted(positions)


def test_get_context_carries_general_terms_from_the_parent(index: TariffIndex) -> None:
    assert "General terms for every service" in index.get_context("1.1.1")


def test_get_context_of_a_top_level_section_equals_get_with_children(index: TariffIndex) -> None:
    assert index.get_context("1") == index.get_with_children("1")


def test_helpers_do_not_mutate_the_index(index: TariffIndex) -> None:
    before = [node.model_dump() for node in index.sections]
    index.get_context("1.1.1")
    index.get_with_children("1")
    assert [node.model_dump() for node in index.sections] == before


def test_page_citation_prefers_the_printed_page(index: TariffIndex) -> None:
    assert index.page_citation("1.1.1") == "1"


def test_page_citation_falls_back_to_the_pdf_page(index: TariffIndex) -> None:
    # Section 2 sits on a spread whose printed footer was unreadable.
    assert index.page_citation("2") == "PDF page 2"


def test_page_citation_when_nothing_is_known() -> None:
    file = TariffIndexFile(
        document_hash="h",
        sections=[SectionNode(id="1", title="T", order=0, pdf_page=None, printed_page=None)],
    )
    assert TariffIndex(file).page_citation("1") == "page unknown"
