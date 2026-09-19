"""Sanity checks over the ingested artifacts.

Structural only: nothing here knows what the document is about, so the checks travel to
any tariff book. They warn; they never change the output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ..models import Charge, SectionNode
from .classifier import content_text, should_classify

NUMBERED_LINE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+\S")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
NUMERIC_TOKEN = re.compile(r"\d")
UNKNOWN_PAGE_RATIO = 0.25


class Level(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    level: Level
    code: str
    message: str


def _page_markers(
    sections: list[SectionNode], page_count: int | None, pages_seen: list[int] | None
) -> list[Finding]:
    # Pages seen while reading the Markdown, not pages where a section happens to start:
    # a page of continuation text begins no section but is not missing.
    if pages_seen is None:
        pages_seen = [node.pdf_page for node in sections if node.pdf_page is not None]
    pages = sorted(set(pages_seen))
    if not pages:
        return [Finding(Level.ERROR, "PAGE_MARKER_MISSING", "No PDF page markers were found")]

    findings = []
    gaps = [page for page in range(pages[0], pages[-1]) if page not in pages]
    if gaps:
        findings.append(
            Finding(Level.ERROR, "PAGE_MARKER_GAP", f"No content for PDF page(s): {gaps}")
        )
    if page_count is not None and pages[-1] != page_count:
        findings.append(
            Finding(
                Level.ERROR,
                "PAGE_MARKER_GAP",
                f"The document has {page_count} pages but content stops at page {pages[-1]}",
            )
        )
    return findings


def _printed_pages(sections: list[SectionNode]) -> list[Finding]:
    if not sections:
        return []
    unknown = sum(1 for node in sections if node.printed_page is None)
    ratio = unknown / len(sections)
    if ratio > UNKNOWN_PAGE_RATIO:
        return [
            Finding(
                Level.WARNING,
                "PRINTED_PAGE_UNKNOWN",
                f"{unknown} of {len(sections)} sections have no printed page number",
            )
        ]
    return []


def _unparsed_headings(sections: list[SectionNode]) -> list[Finding]:
    # A contents page repeats numbers that did become sections elsewhere, so only a number
    # with no section of its own is evidence that a heading was missed.
    known = {node.id for node in sections}
    suspects = []
    for node in sections:
        for line in node.text.splitlines():
            match = NUMBERED_LINE.match(line)
            if match and not TABLE_ROW.match(line) and match.group(1) not in known:
                suspects.append(f"{node.id}: {line.strip()[:60]}")
    if suspects:
        return [
            Finding(
                Level.WARNING,
                "UNPARSED_HEADING",
                f"{len(suspects)} numbered line(s) stayed body text, e.g. {suspects[0]}",
            )
        ]
    return []


def _empty_sections(sections: list[SectionNode]) -> list[Finding]:
    empty = [node.id for node in sections if not content_text(node.text) and not node.children]
    if empty:
        return [
            Finding(Level.WARNING, "EMPTY_SECTION", f"Leaf section(s) with no text: {empty[:10]}")
        ]
    return []


def _sibling_gaps(sections: list[SectionNode]) -> list[Finding]:
    by_parent: dict[str | None, list[int]] = {}
    for node in sections:
        tail = node.id.rsplit(".", 1)[-1]
        if tail.isdigit():
            by_parent.setdefault(node.parent, []).append(int(tail))

    missing = []
    for parent, numbers in by_parent.items():
        ordered = sorted(numbers)
        for value in range(ordered[0], ordered[-1]):
            if value not in numbers:
                missing.append(f"{parent}.{value}" if parent else str(value))
    if missing:
        return [
            Finding(
                Level.WARNING,
                "SIBLING_NUMBER_GAP",
                f"Section number(s) absent between their siblings: {missing[:10]}",
            )
        ]
    return []


def _ragged_tables(sections: list[SectionNode]) -> list[Finding]:
    ragged = []
    for node in sections:
        block: set[int] = set()
        for line in [*node.text.splitlines(), ""]:
            if TABLE_ROW.match(line):
                if not TABLE_RULE.match(line):
                    block.add(len(line.split("|")))
                continue
            if len(block) > 1:
                ragged.append(node.id)
                break
            block = set()
    if ragged:
        return [
            Finding(
                Level.WARNING,
                "RAGGED_TABLE",
                f"Table rows with differing cell counts in: {ragged[:10]}",
            )
        ]
    return []


def _charges(sections: list[SectionNode], charges: list[Charge]) -> list[Finding]:
    if not charges:
        return [Finding(Level.ERROR, "NO_CHARGES_FOUND", "No section was classified as a charge")]

    findings = []
    skipped_leaves = [
        node.id for node in sections if not node.children and not should_classify(node)
    ]
    if skipped_leaves:
        findings.append(
            Finding(
                Level.ERROR,
                "SKIPPED_LEAF",
                f"Leaf section(s) were skipped without being classified: {skipped_leaves[:10]}",
            )
        )

    priced = {charge.section_id for charge in charges}
    silent = [
        node.id
        for node in sections
        if not node.children and node.id not in priced and NUMERIC_TOKEN.search(node.text)
    ]
    if silent:
        findings.append(
            Finding(
                Level.WARNING,
                "NUMERIC_LEAF_NOT_A_CHARGE",
                f"{len(silent)} leaf section(s) hold numbers but define no charge: {silent[:5]}",
            )
        )
    return findings


def run_sanity_checks(
    sections: list[SectionNode],
    charges: list[Charge],
    *,
    page_count: int | None = None,
    pages_seen: list[int] | None = None,
) -> list[Finding]:
    return [
        *_page_markers(sections, page_count, pages_seen),
        *_printed_pages(sections),
        *_unparsed_headings(sections),
        *_empty_sections(sections),
        *_sibling_gaps(sections),
        *_ragged_tables(sections),
        *_charges(sections, charges),
    ]
