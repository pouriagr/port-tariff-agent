"""Step 2: build the section index from the transcribed Markdown.

Pure code, no model call. Hierarchy comes from the dotted section numbers, never from the
heading level, so a mis-levelled heading cannot corrupt the tree.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Set
from dataclasses import dataclass, field

from ..models import SectionNode, TariffIndexFile

BUILDER_VERSION = 2
FRONT_MATTER_ID = "front-matter"
FRONT_MATTER_TITLE = "Front matter"

# A numbered heading, optionally introduced by a word naming the division ("Section 3",
# "Chapter 2"), and optionally with no title of its own because the document prints the
# title separately underneath.
HEADING = re.compile(
    r"^#{1,6}\s+(?:(?P<label>[A-Za-z][A-Za-z-]*)\s+)?(?P<id>\d+(?:\.\d+)*)\.?(?:\s+(?P<title>.+?))?\s*$"
)
PDF_MARKER = re.compile(r"^<!--\s*pdf-page:\s*(\d+)\s*-->\s*$")
PRINTED_MARKER = re.compile(r"^<!--\s*printed-page:\s*(.*?)\s*-->\s*$")
BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class IndexReport:
    """What the builder noticed while reading the document."""

    repeated_ids: list[str] = field(default_factory=list)
    title_conflicts: list[str] = field(default_factory=list)
    distant_repeats: list[str] = field(default_factory=list)
    pages_seen: list[int] = field(default_factory=list)


@dataclass
class _Builder:
    id: str
    title: str
    order: int
    pdf_page: int | None
    printed_page: int | None
    lines: list[str] = field(default_factory=list)
    last_pdf_page: int | None = None


def resolve_parent(section_id: str, known: Set[str]) -> str | None:
    """The nearest ancestor that actually exists, else nothing."""
    if "." not in section_id:
        return None
    parts = section_id.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in known:
            return candidate
    return None


def _clean(lines: Iterable[str]) -> str:
    return BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def build_index(markdown: str, *, document_hash: str) -> tuple[TariffIndexFile, IndexReport]:
    report = IndexReport()
    builders: dict[str, _Builder] = {}
    current: _Builder | None = None
    pdf_page: int | None = None
    printed_page: int | None = None
    order = 0

    for line in markdown.splitlines():
        pdf_match = PDF_MARKER.match(line)
        if pdf_match:
            pdf_page = int(pdf_match.group(1))
            report.pages_seen.append(pdf_page)
            if current is not None:
                current.last_pdf_page = pdf_page
            continue

        printed_match = PRINTED_MARKER.match(line)
        if printed_match:
            raw = printed_match.group(1)
            printed_page = int(raw) if raw.isdigit() else None
            continue

        heading = HEADING.match(line)
        if heading:
            section_id = heading.group("id")
            label, own_title = heading.group("label"), heading.group("title")
            # A heading with no title of its own keeps the words as printed, so the node
            # is still recognisable; the real title follows in the body text.
            title = own_title or " ".join(part for part in (label, section_id) if part)
            existing = builders.get(section_id)
            if existing is None:
                current = _Builder(
                    id=section_id,
                    title=title,
                    order=order,
                    pdf_page=pdf_page,
                    printed_page=printed_page,
                    last_pdf_page=pdf_page,
                )
                builders[section_id] = current
                order += 1
            else:
                # A section continued across a page and the transcription repeated its
                # heading. Append, and keep the first occurrence's page so the citation
                # points at where the section begins.
                report.repeated_ids.append(section_id)
                if existing.title != title:
                    report.title_conflicts.append(section_id)
                if (
                    existing.last_pdf_page is not None
                    and pdf_page is not None
                    and pdf_page - existing.last_pdf_page > 1
                ):
                    report.distant_repeats.append(section_id)
                if existing.lines:
                    existing.lines.append("")
                existing.last_pdf_page = pdf_page
                current = existing
            continue

        if current is None:
            if not line.strip():
                continue
            current = _Builder(
                id=FRONT_MATTER_ID,
                title=FRONT_MATTER_TITLE,
                order=order,
                pdf_page=pdf_page,
                printed_page=printed_page,
                last_pdf_page=pdf_page,
            )
            builders[FRONT_MATTER_ID] = current
            order += 1
        current.lines.append(line)

    known = set(builders)
    nodes = [
        SectionNode(
            id=builder.id,
            title=builder.title,
            parent=resolve_parent(builder.id, known),
            order=builder.order,
            pdf_page=builder.pdf_page,
            printed_page=builder.printed_page,
            text=_clean(builder.lines),
        )
        for builder in sorted(builders.values(), key=lambda item: item.order)
    ]

    by_id = {node.id: node for node in nodes}
    for node in nodes:
        if node.parent is not None:
            by_id[node.parent].children.append(node.id)

    return TariffIndexFile(document_hash=document_hash, sections=nodes), report
