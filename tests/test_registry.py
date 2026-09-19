"""Registry rows, port matching and document selection."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from port_tariff_agent.models import DocumentRow
from port_tariff_agent.paths import DocumentPaths
from port_tariff_agent.registry import (
    covers_date,
    find_row,
    known_ports,
    load_registry,
    mentions_port,
    normalise_port_labels,
    port_key,
    select_document,
    split_combined_label,
    upsert_row,
)
from port_tariff_agent.storage import read_json


def make_row(document_hash: str, **overrides: object) -> DocumentRow:
    payload: dict[str, object] = {
        "document_hash": document_hash,
        "source": "book.pdf",
        "ports": ["Alpha Bay"],
        "page_count": 3,
        "ingested_at": "2026-01-01T00:00:00Z",
    }
    payload.update(overrides)
    return DocumentRow.model_validate(payload)


def test_split_combined_label_handles_every_separator() -> None:
    assert split_combined_label("Alpha Bay / Bravo Point") == ["Alpha Bay", "Bravo Point"]
    assert split_combined_label("Alpha, Bravo; Charlie & Delta") == [
        "Alpha",
        "Bravo",
        "Charlie",
        "Delta",
    ]
    assert split_combined_label("Alpha and Bravo") == ["Alpha", "Bravo"]


def test_split_leaves_real_names_alone() -> None:
    assert split_combined_label("Sainte-Marie") == ["Sainte-Marie"]
    assert split_combined_label("St. John") == ["St. John"]


def test_normalise_splits_trims_capitalises_dedupes_and_sorts() -> None:
    labels = normalise_port_labels(["  bravo  point ", "ALPHA BAY / bravo point", "Alpha Bay"])
    assert labels == ["Alpha Bay", "Bravo Point"]


def test_normalise_drops_noise() -> None:
    assert (
        normalise_port_labels(["", "   ", "-", "a very long label that is not a place name"]) == []
    )


def test_normalise_preserves_internal_capitals() -> None:
    assert normalise_port_labels(["McMurdo"]) == ["McMurdo"]


def test_port_key_is_symmetric_over_case_spacing_and_article() -> None:
    assert port_key("  the Port of  Alpha Bay ") == port_key("ALPHA BAY")


def test_mentions_port_uses_the_normalised_key() -> None:
    row = make_row("h", ports=["Alpha Bay"])
    assert mentions_port(row, "port of alpha bay")
    assert not mentions_port(row, "Alpha")


def test_covers_date_treats_a_missing_bound_as_unbounded() -> None:
    open_row = make_row("h")
    assert covers_date(open_row, date(1999, 1, 1))
    bounded = make_row("h", valid_from="2024-04-01", valid_to="2025-03-31")
    assert covers_date(bounded, date(2024, 11, 15))
    assert not covers_date(bounded, date(2025, 4, 1))
    assert not covers_date(bounded, date(2024, 3, 31))


def test_upsert_creates_the_registry(tmp_path: Path) -> None:
    upsert_row(tmp_path, make_row("aaa"))
    assert DocumentPaths.registry_file(tmp_path).exists()
    assert [row.document_hash for row in load_registry(tmp_path)] == ["aaa"]


def test_upsert_keeps_rows_for_other_documents(tmp_path: Path) -> None:
    upsert_row(tmp_path, make_row("aaa"))
    upsert_row(tmp_path, make_row("bbb"))
    assert {row.document_hash for row in load_registry(tmp_path)} == {"aaa", "bbb"}


def test_upsert_replaces_the_row_for_the_same_document(tmp_path: Path) -> None:
    upsert_row(tmp_path, make_row("aaa", title="first"))
    upsert_row(tmp_path, make_row("aaa", title="second"))
    rows = load_registry(tmp_path)
    assert len(rows) == 1
    assert rows[0].title == "second"


def test_registry_is_written_as_a_json_array_with_the_canonical_field_name(tmp_path: Path) -> None:
    upsert_row(tmp_path, make_row("aaa"))
    payload = read_json(DocumentPaths.registry_file(tmp_path))
    assert isinstance(payload, list)
    assert "document_hash" in payload[0]
    assert "hash" not in payload[0]


def test_reader_accepts_the_legacy_field_name() -> None:
    row = DocumentRow.model_validate(
        {"hash": "old", "source": "s.pdf", "page_count": 1, "ingested_at": "2026-01-01T00:00:00Z"}
    )
    assert row.document_hash == "old"


def test_find_row(tmp_path: Path) -> None:
    upsert_row(tmp_path, make_row("aaa"))
    assert find_row(tmp_path, "aaa") is not None
    assert find_row(tmp_path, "zzz") is None


def test_find_row_on_a_missing_registry(tmp_path: Path) -> None:
    assert find_row(tmp_path, "aaa") is None


def test_select_document_filters_by_port_and_date() -> None:
    rows = [
        make_row("a", ports=["Alpha Bay"], valid_from="2024-04-01", valid_to="2025-03-31"),
        make_row("b", ports=["Bravo Point"], valid_from="2024-04-01", valid_to="2025-03-31"),
    ]
    chosen = select_document(rows, port="alpha bay", on=date(2024, 11, 15))
    assert chosen is not None and chosen.document_hash == "a"


def test_select_document_ignores_inactive_rows() -> None:
    rows = [make_row("a", active=False)]
    assert select_document(rows, port="Alpha Bay", on=date(2024, 11, 15)) is None


def test_select_document_prefers_a_stated_validity_over_an_unknown_one() -> None:
    rows = [
        make_row("open", ingested_at="2026-02-01T00:00:00Z"),
        make_row(
            "bounded",
            valid_from="2024-04-01",
            valid_to="2025-03-31",
            ingested_at="2026-01-01T00:00:00Z",
        ),
    ]
    chosen = select_document(rows, port="Alpha Bay", on=date(2024, 11, 15))
    assert chosen is not None and chosen.document_hash == "bounded"


def test_select_document_tie_breaks_on_newest_ingestion() -> None:
    rows = [
        make_row(
            "older",
            valid_from="2024-04-01",
            valid_to="2025-03-31",
            ingested_at="2026-01-01T00:00:00Z",
        ),
        make_row(
            "newer",
            valid_from="2024-04-01",
            valid_to="2025-03-31",
            ingested_at="2026-02-01T00:00:00Z",
        ),
    ]
    chosen = select_document(rows, port="Alpha Bay", on=date(2024, 11, 15))
    assert chosen is not None and chosen.document_hash == "newer"


def test_known_ports_is_the_normalised_union() -> None:
    rows = [make_row("a", ports=["Alpha Bay"]), make_row("b", ports=["bravo point", "Alpha Bay"])]
    assert known_ports(rows) == ["Alpha Bay", "Bravo Point"]


@pytest.mark.parametrize("port", ["", "   "])
def test_select_document_rejects_an_empty_port(port: str) -> None:
    assert select_document([make_row("a")], port=port, on=date(2024, 11, 15)) is None
