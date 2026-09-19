"""Step 4: reading the document's identity and building the registry row."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from port_tariff_agent.ingestion.profiler import (
    DocumentProfiler,
    RawDocumentProfile,
    build_registry_row,
    parse_currency,
    parse_iso_date,
    to_profile,
)
from port_tariff_agent.models import DocumentProfile
from port_tariff_agent.paths import DocumentPaths
from port_tariff_agent.storage import write_text
from tests.fakes import FakeLlm

RAW = RawDocumentProfile(
    issuer="Some Authority",
    title="Tariff Book, Third Edition",
    currency="zar",
    valid_from="2024-04-01",
    valid_to="2025-03-31",
)


@pytest.fixture
def paths(tmp_path: Path) -> DocumentPaths:
    target = DocumentPaths(data_dir=tmp_path / "data", document_hash="h")
    target.ensure()
    for number in range(1, 5):
        write_text(target.page_md(number), f"page {number} body")
    return target


def test_only_the_front_pages_are_sent(paths: DocumentPaths) -> None:
    llm = FakeLlm(default=RAW)
    DocumentProfiler(llm, model="m").run(paths, page_count=4)

    prompt = llm.request_for("profile/document").prompt
    assert "page 3 body" in prompt
    assert "page 4 body" not in prompt


def test_a_short_document_does_not_crash(paths: DocumentPaths) -> None:
    for number in (2, 3, 4):
        paths.page_md(number).unlink()
    profile = DocumentProfiler(FakeLlm(default=RAW), model="m").run(paths, page_count=1)
    assert profile.issuer == "Some Authority"


def test_the_profile_is_cached(paths: DocumentPaths) -> None:
    DocumentProfiler(FakeLlm(default=RAW), model="m").run(paths, page_count=3)

    second = FakeLlm(default=RAW)
    DocumentProfiler(second, model="m").run(paths, page_count=3)
    assert second.call_count == 0


def test_currency_is_normalised_to_upper_case() -> None:
    assert parse_currency("zar") == "ZAR"
    assert parse_currency("Rand") is None
    assert parse_currency(None) is None


def test_dates_that_do_not_parse_become_null(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_iso_date("1 April 2024") is None
    assert "unparseable date" in caplog.text
    assert parse_iso_date("2024-04-01").isoformat() == "2024-04-01"
    assert parse_iso_date(None) is None


def test_missing_validity_is_stored_as_null_and_warned(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        profile = to_profile(RawDocumentProfile(issuer="X"))
    assert profile.valid_from is None
    assert profile.valid_to is None
    assert "open ended" in caplog.text


def test_registry_row_copies_the_profile_and_normalises_ports() -> None:
    row = build_registry_row(
        document_hash="abc",
        source="book.pdf",
        profile=to_profile(RAW),
        ports_mentioned=["Alpha Bay / Bravo Point", "alpha bay"],
        page_count=27,
        ingested_at="2026-01-01T00:00:00Z",
    )
    assert row.document_hash == "abc"
    assert row.currency == "ZAR"
    assert row.ports == ["Alpha Bay", "Bravo Point"]
    assert row.page_count == 27
    assert row.active is True


def test_registry_row_timestamp_is_utc_with_a_z() -> None:
    row = build_registry_row(
        document_hash="abc",
        source="book.pdf",
        profile=DocumentProfile(),
        ports_mentioned=[],
        page_count=1,
    )
    assert row.ingested_at.endswith("Z")
