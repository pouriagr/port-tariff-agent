"""The README's accuracy and generality blocks, rendered from the recorded runs (ADR-025).

Numbers in a README drift from the code that produced them, and a reader cannot tell. So
the blocks are generated here from the replayed answer and compared against the file by a
test: the figure in the README is, by construction, the figure the recorded run produced,
and it cannot be improved by editing the table.

There is deliberately no `--write` mode. The comparison test puts the replacement text in
its own failure message, so bringing the README up to date is a copy-paste out of a red
test rather than a generator someone has to remember to run.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from tests.test_ground_truth import Reference, Run

ACCURACY_START = "<!-- accuracy-table:start -->"
ACCURACY_END = "<!-- accuracy-table:end -->"
GENERALITY_START = "<!-- generality:start -->"
GENERALITY_END = "<!-- generality:end -->"

HASH_CHARS = 12


class BlockNotFoundError(AssertionError):
    """The README lost its markers, so there is nothing to compare against."""


def extract_block(markdown: str, start: str, end: str) -> str:
    """The text between two markers, markers excluded."""
    try:
        head = markdown.index(start) + len(start)
        tail = markdown.index(end, head)
    except ValueError as exc:
        raise BlockNotFoundError(f"README.md has no {start} ... {end} block") from exc
    return markdown[head:tail].strip()


def rows_of(block: str) -> list[tuple[str, ...]]:
    """Table rows as tuples of trimmed cells, so spacing cannot fail the comparison."""
    rows = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue  # the alignment row carries no information
        rows.append(cells)
    return rows


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _percent(computed: float, expected: float) -> str:
    return f"{(computed - expected) / expected * 100:+.3f} %"


def _provenance(run: Run) -> str:
    agent = run.cassette.prompts["tariff_agent"]
    prompt, output = run.cassette.token_totals
    return (
        f"Recorded {run.cassette.recorded_at[:10]} against `{run.cassette.models['agent']}`"
        f" (agent) and `{run.cassette.models['extract']}` (charge selection), prompt"
        f" `tariff_agent` v{agent.version} sha `{agent.sha}`, document"
        f" `{run.cassette.document_hash[:HASH_CHARS]}`."
        f" {len(run.cassette.interactions)} model calls,"
        f" {prompt:,} prompt and {output:,} output tokens."
    )


def render_accuracy_block(run: Run, references: Sequence[Reference]) -> str:
    """The six reference charges, what the agent computed, and how far apart they are."""
    from tests.test_ground_truth import amounts_by_section

    amounts = amounts_by_section(run.answer)
    lines = [
        _provenance(run),
        "",
        "| Charge | Section | Page | Expected | Computed | Deviation |",
        "|---|---|---|---:|---:|---:|",
    ]
    for ref in references:
        computed = amounts[ref.section_id]
        lines.append(
            f"| {ref.label} | {ref.section_id} | {run.index.page_citation(ref.section_id)}"
            f" | {_money(ref.expected)} | {_money(computed)}"
            f" | {_percent(computed, ref.expected)} |"
        )

    total = sum(amounts[ref.section_id] for ref in references)
    lines.append(f"| **Total** | | | | **{_money(total)}** | |")

    extras = sorted(set(amounts) - {ref.section_id for ref in references})
    lines.append("")
    lines.append(
        "Also priced, beyond the reference table: "
        + (", ".join(f"{sid} ({_money(amounts[sid])})" for sid in extras) if extras else "none")
        + "."
    )
    return "\n".join(lines)


def render_generality_block(durban: Run, other: Run) -> str:
    """What the second port proves: same charges, rates read from its own column."""
    from tests.provenance import differing_sections
    from tests.test_ground_truth import amounts_by_section

    amounts = amounts_by_section(other.answer)
    differing = differing_sections(durban, other)
    lines = [
        _provenance(other),
        "",
        f"| Charge | Section | {other.port} | Rate read from |",
        "|---|---|---:|---|",
    ]
    for section_id in sorted(amounts, key=_sort_key):
        node = other.index.get_node(section_id)
        column = "this port's own column" if section_id in differing else "the same rates"
        lines.append(
            f"| {node.title if node else section_id} | {section_id}"
            f" | {_money(amounts[section_id])} | {column} |"
        )
    lines.append(f"| **Total** | | **{_money(sum(amounts.values()))}** | |")
    lines.append("")
    lines.append(
        f"Sections where {other.port} and {durban.port} resolved to different constants:"
        f" {', '.join(sorted(differing, key=_sort_key)) if differing else 'none'}."
    )
    return "\n".join(lines)


def _sort_key(section_id: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in section_id.split("."))


def replacement_message(name: str, rendered: str) -> str:
    return (
        f"README.md's {name} block is out of date."
        f" Replace what is between the markers with:\n\n{rendered}\n"
    )
