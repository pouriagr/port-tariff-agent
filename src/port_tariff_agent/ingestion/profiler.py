"""Step 4: read what the document is, and write the registry row."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime

from pydantic import BaseModel

from ..llm.protocol import StructuredGenerator
from ..models import DocumentProfile, DocumentRow
from ..paths import DocumentPaths
from ..prompts import DOCUMENT_PROFILE
from ..registry import normalise_port_labels
from ..storage import read_json, read_text, write_json

log = logging.getLogger(__name__)

STEP = "profile"
FRONT_PAGES = 3
CURRENCY = re.compile(r"^[A-Za-z]{3}$")


class RawDocumentProfile(BaseModel):
    """Permissive on purpose: a strict date here would burn every retry on a value a
    two-line parser can handle."""

    issuer: str | None = None
    title: str | None = None
    currency: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


def parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        log.warning("ignoring unparseable date %r", raw)
        return None


def parse_currency(raw: str | None) -> str | None:
    if raw and CURRENCY.match(raw.strip()):
        return raw.strip().upper()
    if raw:
        log.warning("ignoring unrecognised currency %r", raw)
    return None


def to_profile(raw: RawDocumentProfile) -> DocumentProfile:
    profile = DocumentProfile(
        issuer=raw.issuer or None,
        title=raw.title or None,
        currency=parse_currency(raw.currency),
        valid_from=parse_iso_date(raw.valid_from),
        valid_to=parse_iso_date(raw.valid_to),
    )
    if profile.valid_from is None or profile.valid_to is None:
        log.warning("document validity is open ended; selection will treat it as unbounded")
    return profile


def front_pages(paths: DocumentPaths, page_count: int) -> str:
    texts = [
        read_text(paths.page_md(number))
        for number in range(1, min(FRONT_PAGES, page_count) + 1)
        if paths.page_md(number).exists()
    ]
    return "\n\n".join(texts)


class DocumentProfiler:
    def __init__(self, client: StructuredGenerator, *, model: str) -> None:
        self._client = client
        self._model = model

    def run(
        self,
        paths: DocumentPaths,
        *,
        page_count: int,
        reuse_cache: bool = True,
    ) -> DocumentProfile:
        if reuse_cache and paths.profile_json.exists():
            return DocumentProfile.model_validate(read_json(paths.profile_json))

        result = self._client.generate_structured(
            call_id=f"{STEP}/document",
            model=self._model,
            schema=RawDocumentProfile,
            prompt=f"{DOCUMENT_PROFILE.text}\n\n{front_pages(paths, page_count)}",
        )
        profile = to_profile(result.value)
        write_json(paths.profile_json, profile)
        return profile


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_registry_row(
    *,
    document_hash: str,
    source: str,
    profile: DocumentProfile,
    ports_mentioned: Iterable[str],
    page_count: int,
    ingested_at: str | None = None,
) -> DocumentRow:
    return DocumentRow(
        document_hash=document_hash,
        source=source,
        issuer=profile.issuer,
        title=profile.title,
        currency=profile.currency,
        valid_from=profile.valid_from,
        valid_to=profile.valid_to,
        ports=normalise_port_labels(ports_mentioned),
        page_count=page_count,
        ingested_at=ingested_at or utc_now_iso(),
        active=True,
    )
