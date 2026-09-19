"""Step 1: caching, resume, failure handling and the join format."""

from __future__ import annotations

from pathlib import Path

import pytest

from port_tariff_agent.errors import LlmError
from port_tariff_agent.ingestion.pdf import split_pages
from port_tariff_agent.ingestion.transcriber import (
    PageTranscriber,
    PageTranscription,
    join_pages,
)
from port_tariff_agent.paths import DocumentPaths
from tests.fakes import FakeLlm

PAGES = [b"one", b"two", b"three"]


@pytest.fixture
def paths(tmp_path: Path) -> DocumentPaths:
    return DocumentPaths(data_dir=tmp_path / "data", document_hash="h")


def page_text(request) -> PageTranscription:
    number = request.call_id.split("_")[-1]
    return PageTranscription(markdown=f"<!-- printed-page: {int(number)} -->\nbody {int(number)}")


def test_one_call_per_page_and_files_written(paths: DocumentPaths) -> None:
    llm = FakeLlm(default=page_text)
    report = PageTranscriber(llm, model="m", max_workers=2).run(PAGES, paths)

    assert llm.call_count == 3
    assert llm.call_ids == sorted(llm.call_ids)
    assert report.transcribed == [1, 2, 3]
    assert paths.page_md(1).read_text(encoding="utf-8").startswith("<!-- printed-page: 1 -->")


def test_each_request_carries_exactly_its_own_page(paths: DocumentPaths) -> None:
    llm = FakeLlm(default=page_text)
    PageTranscriber(llm, model="m", max_workers=1).run(PAGES, paths)

    request = llm.request_for("transcribe/page_002")
    assert [file.data for file in request.files] == [b"two"]
    assert request.files[0].mime_type == "application/pdf"


def test_cached_pages_are_not_requested_again(paths: DocumentPaths) -> None:
    transcriber = PageTranscriber(FakeLlm(default=page_text), model="m")
    transcriber.run(PAGES, paths)

    second = FakeLlm(default=page_text)
    report = PageTranscriber(second, model="m").run(PAGES, paths)
    assert second.call_count == 0
    assert report.reused == [1, 2, 3]


def test_resume_requests_only_the_missing_page(paths: DocumentPaths) -> None:
    PageTranscriber(FakeLlm(default=page_text), model="m").run(PAGES, paths)
    paths.page_md(2).unlink()

    second = FakeLlm(default=page_text)
    PageTranscriber(second, model="m").run(PAGES, paths)
    assert second.call_ids == ["transcribe/page_002"]


def test_ignoring_the_cache_re_requests_every_page(paths: DocumentPaths) -> None:
    PageTranscriber(FakeLlm(default=page_text), model="m").run(PAGES, paths)

    second = FakeLlm(default=page_text)
    PageTranscriber(second, model="m").run(PAGES, paths, reuse_cache=False)
    assert second.call_count == 3


def test_a_failed_page_writes_an_error_file_and_blocks_the_join(paths: DocumentPaths) -> None:
    def responder(request):
        if request.call_id.endswith("002"):
            raise LlmError("page blew up", retryable=False)
        return page_text(request)

    report = PageTranscriber(FakeLlm(default=responder), model="m").run(PAGES, paths)

    assert report.failed == [2]
    assert "page blew up" in paths.page_error(2).read_text(encoding="utf-8")
    assert not paths.tariff_md.exists()


def test_a_successful_retry_clears_the_error_file(paths: DocumentPaths) -> None:
    def flaky(request):
        if request.call_id.endswith("002"):
            raise LlmError("temporary", retryable=False)
        return page_text(request)

    PageTranscriber(FakeLlm(default=flaky), model="m").run(PAGES, paths)
    assert paths.page_error(2).exists()

    PageTranscriber(FakeLlm(default=page_text), model="m").run(PAGES, paths)
    assert not paths.page_error(2).exists()
    assert paths.tariff_md.exists()


def test_an_empty_response_counts_as_a_failure(paths: DocumentPaths) -> None:
    llm = FakeLlm(default=PageTranscription(markdown="   "))
    report = PageTranscriber(llm, model="m").run(PAGES, paths)
    assert report.failed == [1, 2, 3]


def test_a_page_without_a_printed_marker_is_reported_not_failed(paths: DocumentPaths) -> None:
    llm = FakeLlm(default=PageTranscription(markdown="body with no marker"))
    report = PageTranscriber(llm, model="m").run(PAGES, paths)
    assert report.failed == []
    assert report.without_page_marker == [1, 2, 3]


def test_concurrency_is_bounded(paths: DocumentPaths) -> None:
    llm = FakeLlm(default=page_text)
    PageTranscriber(llm, model="m", max_workers=2).run(PAGES, paths)
    assert llm.max_in_flight <= 2


def test_join_format_is_marker_body_blank_line() -> None:
    joined = join_pages([(2, "second"), (1, "first")])
    assert joined.splitlines() == [
        "<!-- pdf-page: 001 -->",
        "first",
        "",
        "<!-- pdf-page: 002 -->",
        "second",
        "",
    ]


def test_join_orders_numerically_past_page_nine() -> None:
    joined = join_pages([(10, "ten"), (9, "nine")])
    assert joined.index("<!-- pdf-page: 009 -->") < joined.index("<!-- pdf-page: 010 -->")


def test_tariff_md_is_written_once_every_page_succeeds(paths: DocumentPaths) -> None:
    PageTranscriber(FakeLlm(default=page_text), model="m").run(PAGES, paths)
    text = paths.tariff_md.read_text(encoding="utf-8")
    assert text.startswith("<!-- pdf-page: 001 -->")
    assert "body 3" in text


def test_split_pages_round_trips_a_real_pdf(tiny_pdf) -> None:
    pages = split_pages(tiny_pdf(4).read_bytes())
    assert len(pages) == 4
    assert all(page.startswith(b"%PDF") for page in pages)
