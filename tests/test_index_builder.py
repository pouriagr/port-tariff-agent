"""The index builder is pure code, so it carries the bulk of the unit tests."""

from __future__ import annotations

from collections.abc import Callable

from port_tariff_agent.ingestion.index_builder import FRONT_MATTER_ID, build_index, resolve_parent

HASH = "abc123"


def build(markdown: str):
    return build_index(markdown, document_hash=HASH)


def ids(index) -> list[str]:
    return [node.id for node in index.sections]


def node(index, section_id: str):
    return next(item for item in index.sections if item.id == section_id)


def test_front_matter_collects_the_preamble(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    front = node(index, FRONT_MATTER_ID)
    assert front.order == 0
    assert front.parent is None
    assert "This schedule applies to all craft" in front.text


def test_no_front_matter_node_when_document_starts_with_a_heading(
    load_markdown: Callable[[str], str],
) -> None:
    index, _ = build(load_markdown("no_front_matter"))
    assert FRONT_MATTER_ID not in ids(index)


def test_ids_and_titles_are_captured_verbatim(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert node(index, "1.1").title == "WIDGET HANDLING"
    assert node(index, "1.1.1").title == "WIDGET HANDLING, NIGHT RATE"


def test_depth_comes_from_the_number_not_the_heading_level(
    load_markdown: Callable[[str], str],
) -> None:
    index, _ = build(load_markdown("simple"))
    # "### 2.1 FORMS" is mis-levelled in the source; the hierarchy must ignore that.
    assert node(index, "2.1").parent == "2"
    assert node(index, "2.1").depth == 2


def test_unnumbered_heading_stays_body_text(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert "**Working hours**" in node(index, "1").text
    assert all(item.id == FRONT_MATTER_ID or item.id[0].isdigit() for item in index.sections)


def test_numbered_line_without_hashes_is_not_a_heading() -> None:
    index, _ = build("# 5 THING\n\n5.1 This line is prose, not a heading.\n")
    assert ids(index) == ["5"]


def test_repeated_heading_appends_to_the_existing_node(
    load_markdown: Callable[[str], str],
) -> None:
    index, report = build(load_markdown("simple"))
    assert ids(index).count("1.1") == 1
    text = node(index, "1.1").text
    assert "Minimum fee: 235.52" in text
    assert "Continued from the previous page" in text
    assert "1.1" in report.repeated_ids


def test_repeated_heading_keeps_the_first_occurrence_pages(
    load_markdown: Callable[[str], str],
) -> None:
    index, _ = build(load_markdown("simple"))
    section = node(index, "1.1")
    assert section.pdf_page == 1
    assert section.printed_page == 1


def test_parent_is_the_immediate_prefix(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert node(index, "1.1.1").parent == "1.1"


def test_parent_falls_back_to_the_longest_existing_prefix(
    load_markdown: Callable[[str], str],
) -> None:
    index, _ = build(load_markdown("simple"))
    # There is no node 1.2, so 1.2.3 hangs off 1.
    assert node(index, "1.2.3").parent == "1"
    assert "1.2.3" in node(index, "1").children


def test_parent_is_none_when_no_prefix_exists(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("orphan_parent"))
    assert node(index, "4.2").parent is None


def test_children_are_in_document_order(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert node(index, "1").children == ["1.1", "1.2.3"]


def test_order_is_dense_and_follows_the_document(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert [item.order for item in index.sections] == list(range(len(index.sections)))


def test_pdf_page_tracked_from_markers(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert node(index, "2").pdf_page == 2


def test_two_printed_pages_inside_one_pdf_page(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert node(index, "1.1.1").printed_page == 1
    assert node(index, "1.2.3").printed_page == 2


def test_unknown_printed_page_becomes_none(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert node(index, "2").printed_page is None
    assert node(index, "2").pdf_page == 2


def test_markers_never_appear_in_section_text(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert all("<!--" not in item.text for item in index.sections)


def test_heading_is_not_part_of_its_own_text(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    assert not node(index, "1.1").text.startswith("## 1.1")


def test_table_rows_survive_verbatim(load_markdown: Callable[[str], str]) -> None:
    index, _ = build(load_markdown("simple"))
    lines = node(index, "1.1").text.splitlines()
    assert "| Up to 10 000 units | 1 234.56 | n/a | 987.65 |" in lines


def test_crlf_input_gives_the_same_index(load_markdown: Callable[[str], str]) -> None:
    markdown = load_markdown("simple")
    lf, _ = build(markdown)
    crlf, _ = build(markdown.replace("\n", "\r\n"))
    assert lf.model_dump() == crlf.model_dump()


def test_trailing_dot_after_the_number_is_accepted() -> None:
    index, _ = build("## 7.2. A TITLE\n\nBody.\n")
    assert ids(index) == ["7.2"]


def test_empty_document_produces_no_sections() -> None:
    index, _ = build("")
    assert index.sections == []
    assert index.document_hash == HASH


def test_resolve_parent_is_pure() -> None:
    assert resolve_parent("1.2.3", {"1", "1.2"}) == "1.2"
    assert resolve_parent("1.2.3", {"1"}) == "1"
    assert resolve_parent("1.2.3", set()) is None
    assert resolve_parent("4", {"4"}) is None


def test_a_heading_that_names_its_division_still_yields_the_number() -> None:
    # Documents often print "SECTION 3" on its own line with the title underneath.
    index, _ = build("# SECTION 3\n\n**MARINE SERVICES**\n\n## 3.1 GENERAL TERMS\n\nBody.\n")
    assert ids(index) == ["3", "3.1"]
    assert node(index, "3.1").parent == "3"
    assert "**MARINE SERVICES**" in node(index, "3").text


def test_a_heading_without_its_own_title_keeps_the_printed_words() -> None:
    index, _ = build("# SECTION 3\n\nBody.\n")
    assert node(index, "3").title == "SECTION 3"


def test_a_bare_number_heading_is_accepted() -> None:
    index, _ = build("# 4\n\nBody.\n")
    assert node(index, "4").title == "4"


def test_general_terms_in_a_sibling_reach_a_charge_through_their_parent() -> None:
    markdown = (
        "# SECTION 3\n\n**MARINE SERVICES**\n\n"
        "## 3.1 GENERAL TERMS\n\nWorking hours apply.\n\n"
        "## 3.3 A SERVICE\n\nRate table.\n"
    )
    index, _ = build(markdown)
    assert node(index, "3.3").parent == "3"
    assert node(index, "3").children == ["3.1", "3.3"]
