"""The document registry, and the port matching that makes it searchable.

Shared with the query phase: ingestion appends rows, the agent selects one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from .models import DocumentRow
from .paths import DocumentPaths
from .storage import read_json, write_json

LABEL_SEPARATORS = re.compile(r"\s*(?:/|,|;|&|\band\b)\s*", re.IGNORECASE)
PUNCTUATION_EDGES = re.compile(r"^[^\w(]+|[^\w)]+$")
WHITESPACE = re.compile(r"\s+")
LEADING_ARTICLE = re.compile(r"^(?:the\s+)?port\s+of\s+", re.IGNORECASE)
MAX_LABEL_WORDS = 4


def split_combined_label(raw: str) -> list[str]:
    """Split a label that names several places into one entry each."""
    return [part for part in (piece.strip() for piece in LABEL_SEPARATORS.split(raw)) if part]


def _capitalise(label: str) -> str:
    # str.title() mangles apostrophes and internal capitals, so touch only the first
    # character of tokens that are entirely one case.
    words = []
    for word in label.split(" "):
        if word.islower() or word.isupper():
            words.append(word[:1].upper() + word[1:].lower())
        else:
            words.append(word)
    return " ".join(words)


def normalise_port_labels(values: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        for part in split_combined_label(value):
            cleaned = WHITESPACE.sub(" ", PUNCTUATION_EDGES.sub("", part)).strip()
            if not cleaned or len(cleaned.split(" ")) > MAX_LABEL_WORDS:
                continue
            key = port_key(cleaned)
            if key and key not in seen:
                seen[key] = _capitalise(cleaned)
    return sorted(seen.values())


def port_key(value: str) -> str:
    """Comparison key, applied to both the stored label and the queried port."""
    cleaned = WHITESPACE.sub(" ", PUNCTUATION_EDGES.sub("", value)).strip()
    return LEADING_ARTICLE.sub("", cleaned).casefold()


def mentions_port(row: DocumentRow, port: str) -> bool:
    wanted = port_key(port)
    return any(port_key(known) == wanted for known in row.ports)


def covers_date(row: DocumentRow, on: date) -> bool:
    """A missing bound is unbounded on that side."""
    if row.valid_from is not None and on < row.valid_from:
        return False
    return not (row.valid_to is not None and on > row.valid_to)


def has_bounded_validity(row: DocumentRow) -> bool:
    return row.valid_from is not None or row.valid_to is not None


def load_registry(data_dir: Path) -> list[DocumentRow]:
    path = DocumentPaths.registry_file(data_dir)
    if not path.exists():
        return []
    return [DocumentRow.model_validate(row) for row in read_json(path)]


def save_registry(data_dir: Path, rows: list[DocumentRow]) -> None:
    write_json(
        DocumentPaths.registry_file(data_dir), [row.model_dump(by_alias=True) for row in rows]
    )


def find_row(data_dir: Path, document_hash: str) -> DocumentRow | None:
    for row in load_registry(data_dir):
        if row.document_hash == document_hash:
            return row
    return None


def upsert_row(data_dir: Path, row: DocumentRow) -> None:
    """Append the row, replacing any earlier row for the same document.

    A new edition is a different hash and therefore a different row; re-ingesting the same
    bytes must not accumulate duplicates.
    """
    rows = [
        existing
        for existing in load_registry(data_dir)
        if existing.document_hash != row.document_hash
    ]
    rows.append(row)
    save_registry(data_dir, rows)


def known_ports(rows: Iterable[DocumentRow]) -> list[str]:
    return normalise_port_labels(port for row in rows for port in row.ports)


def select_document(rows: Iterable[DocumentRow], *, port: str, on: date) -> DocumentRow | None:
    """The document that covers this port on this date, newest ingestion first.

    A row whose stated validity contains the date beats one that only matches because its
    validity is unknown.
    """
    candidates = [
        row for row in rows if row.active and mentions_port(row, port) and covers_date(row, on)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (has_bounded_validity(row), row.ingested_at), reverse=True)
    return candidates[0]
