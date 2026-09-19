"""Splitting the source PDF into single pages."""

from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from ..errors import IngestionError


def read_pdf(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise IngestionError(f"Cannot read {path}: {exc}") from exc


def split_pages(pdf_bytes: bytes) -> list[bytes]:
    """One single-page PDF per page, in document order."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            writer = PdfWriter()
            writer.add_page(page)
            buffer = io.BytesIO()
            writer.write(buffer)
            pages.append(buffer.getvalue())
    except (PdfReadError, OSError, ValueError) as exc:
        raise IngestionError(f"Not a readable PDF: {exc}") from exc
    if not pages:
        raise IngestionError("The PDF has no pages")
    return pages
