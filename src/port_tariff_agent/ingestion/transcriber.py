"""Step 1: turn each PDF page into Markdown."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from ..concurrency import map_bounded
from ..errors import LlmError
from ..llm.protocol import InlineFile, StructuredGenerator
from ..paths import DocumentPaths
from ..prompts import PAGE_TRANSCRIPTION
from ..storage import read_text, write_text

log = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
PAGE_MARKER = "<!-- pdf-page: {page:03d} -->"
PRINTED_MARKER_HINT = "printed-page:"
STEP = "transcribe"

ProgressFn = Callable[[str, int, int], None]


class PageTranscription(BaseModel):
    markdown: str


@dataclass
class TranscriptionReport:
    transcribed: list[int] = field(default_factory=list)
    reused: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    without_page_marker: list[int] = field(default_factory=list)


def join_pages(page_texts: Sequence[tuple[int, str]]) -> str:
    """One marker per page, then its body, then a blank line."""
    return "".join(
        f"{PAGE_MARKER.format(page=number)}\n{text.strip()}\n\n"
        for number, text in sorted(page_texts, key=lambda item: item[0])
    )


class PageTranscriber:
    def __init__(
        self,
        client: StructuredGenerator,
        *,
        model: str,
        max_workers: int = 4,
    ) -> None:
        self._client = client
        self._model = model
        self._max_workers = max_workers

    def transcribe_page(self, page_pdf: bytes, page_number: int) -> str:
        result = self._client.generate_structured(
            call_id=f"{STEP}/page_{page_number:03d}",
            model=self._model,
            schema=PageTranscription,
            prompt=PAGE_TRANSCRIPTION.text,
            files=[InlineFile(mime_type=PDF_MIME, data=page_pdf)],
        )
        markdown = result.value.markdown.strip()
        if not markdown:
            raise LlmError(f"page {page_number} came back empty", retryable=True)
        return markdown

    def run(
        self,
        pages: Sequence[bytes],
        paths: DocumentPaths,
        *,
        reuse_cache: bool = True,
        progress: ProgressFn | None = None,
    ) -> TranscriptionReport:
        report = TranscriptionReport()
        paths.ensure()

        pending: list[int] = []
        for number in range(1, len(pages) + 1):
            cached = paths.page_md(number)
            if reuse_cache and cached.exists():
                report.reused.append(number)
            else:
                pending.append(number)

        done = len(report.reused)
        total = len(pages)
        if progress is not None:
            progress(STEP, done, total)

        def work(number: int) -> str:
            return self.transcribe_page(pages[number - 1], number)

        def succeeded(number: int, markdown: str) -> None:
            nonlocal done
            write_text(paths.page_md(number), markdown + "\n")
            paths.page_error(number).unlink(missing_ok=True)
            report.transcribed.append(number)
            if PRINTED_MARKER_HINT not in markdown:
                report.without_page_marker.append(number)
            done += 1
            if progress is not None:
                progress(STEP, done, total)

        def failed(number: int, exc: BaseException) -> None:
            nonlocal done
            write_text(paths.page_error(number), f"{type(exc).__name__}: {exc}\n")
            report.failed.append(number)
            log.warning("page %s failed: %s", number, exc)
            done += 1
            if progress is not None:
                progress(STEP, done, total)

        map_bounded(
            pending,
            work,
            max_workers=self._max_workers,
            on_success=succeeded,
            on_failure=failed,
        )

        report.transcribed.sort()
        report.failed.sort()
        report.without_page_marker.sort()

        if not report.failed:
            texts = [
                (number, read_text(paths.page_md(number))) for number in range(1, len(pages) + 1)
            ]
            write_text(paths.tariff_md, join_pages(texts))

        return report
