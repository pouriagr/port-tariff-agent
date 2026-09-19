"""Where did each rate come from (ADR-024)?

A second port has no reference values to check against, so generality is asserted
structurally instead: every rate in a formula has to be traceable to the section the
answer cites, and to the column of that section's table which belongs to this port — or,
when the table has no column for this port, to a column that belongs to no port at all.

Nothing here knows a port name, a charge name or a rate. "A header that names a port" is
decided against the registry row's own `ports` list, which ingestion derived from the
document. Point the system at another tariff book and these checks still mean something.

Three layers:

* `check_provenance` - the literal appears in the cited section's text.
* `check_columns`    - it appears in the right column of that section's tables.
* `differing_sections` - two ports did not resolve to the same constants.

Layer A alone is too weak to be the generality test on its own: a run that read the wrong
port's column still passes it, because the wrong column is in the same section. Layer B is
what catches that.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from port_tariff_agent.registry import port_key, split_combined_label

if TYPE_CHECKING:  # pragma: no cover - types only
    from tests.test_ground_truth import Run

DIGIT_GROUP_SPACE = re.compile(r"(?<=\d)[    ,](?=\d)")
"""A thousands separator as this document prints it: a space, or a comma in a query."""

RATE = re.compile(r"(?<!\d)\d+\.\d{2}(?!\d)")
"""What a rate looks like once normalised: a money amount, exactly two decimals.

Restricting to two decimals is what keeps structural numbers out of the check - the 100 of
"per 100 tons", the 2 of two services, a band floor, an intermediate the model derived.
Those are not quoted from the document and have no business being looked for in it.
"""

NUMBER = re.compile(r"\d+(?:\.\d+)?")


def normalise(text: str) -> str:
    """Join digit groups printed with a separator, so `2 801.91` and `2801.91` compare."""
    return DIGIT_GROUP_SPACE.sub("", text)


def rate_literals(text: str) -> set[str]:
    return set(RATE.findall(normalise(text)))


def stated_numbers(text: str) -> set[str]:
    """Every number the user put in the question.

    Subtracted from a formula's literals, this is what excludes the vessel's own figures -
    the tonnage, the days alongside - without an ignore list anyone has to maintain.
    """
    return set(NUMBER.findall(normalise(text)))


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    cells: tuple[str, ...]

    def names_one_of(self, ports: Iterable[str]) -> bool:
        keys = {port_key(port) for port in ports}
        return any(port_key(label) in keys for label in split_combined_label(self.header))

    def holds(self, literal: str) -> bool:
        return any(literal in rate_literals(cell) for cell in self.cells)


@dataclass(frozen=True, slots=True)
class Table:
    columns: tuple[Column, ...]

    def holds(self, literal: str) -> bool:
        return any(column.holds(literal) for column in self.columns)

    def columns_naming(self, ports: Iterable[str]) -> list[Column]:
        return [column for column in self.columns if column.names_one_of(ports)]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_alignment(cells: Sequence[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells)


def parse_tables(text: str) -> list[Table]:
    """Pipe tables in the given text, as header plus the cells underneath each header.

    A table whose ports are listed down the first column rather than across the header is
    not recognised, and simply yields no port columns; `check_columns` then has nothing to
    say about it. This document's port tables are column-oriented (`docs/spec/pdf-notes.md`).
    """
    tables: list[Table] = []
    block: list[list[str]] = []

    def flush() -> None:
        if len(block) >= 2 and _is_alignment(block[1]):
            header = block[0]
            body = block[2:]
            tables.append(
                Table(
                    columns=tuple(
                        Column(
                            header=header[index],
                            cells=tuple(row[index] for row in body if index < len(row)),
                        )
                        for index in range(len(header))
                    )
                )
            )
        block.clear()

    for line in text.splitlines():
        if line.strip().startswith("|"):
            block.append(_cells(line))
        else:
            flush()
    flush()
    return tables


def formula_literals(run: Run, section_id: str) -> set[str]:
    """The rates a run quoted for one section, with the question's own numbers removed."""
    stated = stated_numbers(run.cassette.query)
    literals: set[str] = set()
    for line in run.answer.charges:
        if line.section_id == section_id:
            literals |= rate_literals(line.formula) - stated
    return literals


def check_provenance(run: Run, *, only: Sequence[str] | None = None) -> list[str]:
    """Rates that are not in the text of the section the answer cites them from."""
    stated = stated_numbers(run.cassette.query)
    problems = []
    for line in run.answer.charges:
        if only is not None and line.section_id not in only:
            continue
        available = rate_literals(run.context_for(line.section_id))
        for literal in sorted(rate_literals(line.formula) - stated):
            if literal not in available:
                problems.append(
                    f"{line.section_id} ({line.name}): {literal} is not in that section's"
                    f" text. Formula: {line.formula}"
                )
    return problems


def check_columns(run: Run) -> list[str]:
    """Rates taken from another port's column, or from a port column that is not this port's.

    Only literals that actually appear somewhere in one of the section's tables are
    judged; anything else was already answered by `check_provenance`.
    """
    problems = []
    for line in run.answer.charges:
        node = run.index.get_node(line.section_id)
        if node is None:
            continue
        for table in parse_tables(node.text):
            problems.extend(_check_table(run, line.section_id, line.name, table))
    return problems


def _check_table(run: Run, section_id: str, name: str, table: Table) -> list[str]:
    ours = table.columns_naming([run.port])
    all_ports = table.columns_naming(run.row.ports)
    problems = []

    for literal in sorted(formula_literals(run, section_id)):
        if not table.holds(literal):
            continue
        if ours:
            if not any(column.holds(literal) for column in ours):
                problems.append(
                    f"{section_id} ({name}): {literal} was read from"
                    f" {_where(table, literal)}, but the table has a"
                    f" {run.port!r} column"
                )
        elif all_ports:
            fallback = [column for column in table.columns if column not in all_ports]
            if not any(column.holds(literal) for column in fallback):
                problems.append(
                    f"{section_id} ({name}): the table has no {run.port!r} column,"
                    f" so {literal} should come from a column naming no port,"
                    f" not from {_where(table, literal)}"
                )
    return problems


def _where(table: Table, literal: str) -> str:
    holders = [column.header or "(unlabelled)" for column in table.columns if column.holds(literal)]
    return ", ".join(repr(header) for header in holders)


def differing_sections(first: Run, second: Run) -> set[str]:
    """Sections both runs priced, where they quoted different rates.

    Non-empty is the proof that the column was resolved from the document rather than
    fixed in code: the same question at two ports cannot produce the same constants
    everywhere unless the table has no port columns at all.
    """
    from tests.test_ground_truth import amounts_by_section

    shared = set(amounts_by_section(first.answer)) & set(amounts_by_section(second.answer))
    return {
        section_id
        for section_id in shared
        if formula_literals(first, section_id) != formula_literals(second, section_id)
    }
