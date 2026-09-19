"""Ingest one PDF: four steps, each resumable from what the last one left on disk."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ClassificationError, TranscriptionError
from ..llm.protocol import StructuredGenerator
from ..models import DocumentRow, Manifest
from ..paths import DocumentPaths
from ..prompts import CHARGE_CLASSIFICATION, DOCUMENT_PROFILE, PAGE_TRANSCRIPTION
from ..registry import find_row, upsert_row
from ..settings import Settings
from ..storage import read_json, sha256_bytes, write_json
from ..tariff_index import TariffIndex
from .checks import Finding, run_sanity_checks
from .classifier import ChargeClassifier
from .index_builder import BUILDER_VERSION, build_index
from .pdf import read_pdf, split_pages
from .profiler import DocumentProfiler, build_registry_row
from .transcriber import PageTranscriber

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]


@dataclass
class IngestResult:
    document_hash: str
    paths: DocumentPaths
    row: DocumentRow
    already_ingested: bool = False
    page_count: int = 0
    section_count: int = 0
    classified: int = 0
    skipped: int = 0
    charge_count: int = 0
    findings: list[Finding] = field(default_factory=list)


def _step_inputs(settings: Settings) -> dict[str, tuple[int, str, str]]:
    """What each step's output depends on: its instructions and who carried them out.

    The index builder has no prompt and no model, so it is keyed on its own version; that
    is what lets a change to the parsing rules re-run step 2 without paying for step 1
    again.
    """
    return {
        "transcribe": (
            PAGE_TRANSCRIPTION.version,
            PAGE_TRANSCRIPTION.sha,
            settings.gemini_model_extract,
        ),
        "index": (BUILDER_VERSION, "", ""),
        "classify": (
            CHARGE_CLASSIFICATION.version,
            CHARGE_CLASSIFICATION.sha,
            settings.gemini_model_classify,
        ),
        "profile": (DOCUMENT_PROFILE.version, DOCUMENT_PROFILE.sha, settings.gemini_model_extract),
    }


def _load_manifest(paths: DocumentPaths, document_hash: str) -> Manifest:
    if paths.manifest_json.exists():
        return Manifest.model_validate(read_json(paths.manifest_json))
    return Manifest(document_hash=document_hash)


def ingest_document(
    pdf_path: Path,
    *,
    settings: Settings,
    client: StructuredGenerator,
    force: bool = False,
    progress: ProgressFn | None = None,
) -> IngestResult:
    pdf_bytes = read_pdf(pdf_path)
    document_hash = sha256_bytes(pdf_bytes)
    paths = DocumentPaths(data_dir=settings.data_dir, document_hash=document_hash)

    manifest = _load_manifest(paths, document_hash)
    stale_steps = [
        step
        for step, (version, sha, model) in _step_inputs(settings).items()
        if not manifest.matches(step, prompt_version=version, prompt_sha=sha, model=model)
    ]

    existing = find_row(settings.data_dir, document_hash)
    if existing is not None and not force and not stale_steps:
        # The row is written only by the last step, so its presence means the whole
        # ingestion finished.
        return IngestResult(
            document_hash=document_hash,
            paths=paths,
            row=existing,
            already_ingested=True,
            page_count=existing.page_count,
        )
    if stale_steps:
        log.info("re-running step(s) whose prompt or model changed: %s", stale_steps)

    paths.ensure()
    if not paths.source_pdf.exists():
        shutil.copyfile(pdf_path, paths.source_pdf)

    pages = split_pages(pdf_bytes)
    workers = settings.ingest_concurrency

    # Step 1: transcription.
    transcribe_fresh = force or "transcribe" in stale_steps
    transcription = PageTranscriber(
        client, model=settings.gemini_model_extract, max_workers=workers
    ).run(pages, paths, reuse_cache=not transcribe_fresh, progress=progress)
    if transcription.failed:
        write_json(paths.manifest_json, manifest)
        raise TranscriptionError(transcription.failed)
    manifest.record(
        "transcribe",
        prompt_version=PAGE_TRANSCRIPTION.version,
        prompt_sha=PAGE_TRANSCRIPTION.sha,
        model=settings.gemini_model_extract,
    )
    if transcription.without_page_marker:
        log.warning("no printed page marker on page(s): %s", transcription.without_page_marker)

    # Step 2: the index. Pure code and cheap, so always rebuilt from the current Markdown.
    index_file, index_report = build_index(
        paths.tariff_md.read_text(encoding="utf-8"), document_hash=document_hash
    )
    write_json(paths.index_json, index_file)
    manifest.record("index", prompt_version=BUILDER_VERSION, prompt_sha="", model="")
    index = TariffIndex(index_file)
    if index_report.distant_repeats:
        log.warning(
            "section id(s) repeated far apart, which may be two different sections: %s",
            index_report.distant_repeats,
        )

    # Step 3: the charge catalog.
    classify_fresh = force or "classify" in stale_steps
    classification = ChargeClassifier(
        client, model=settings.gemini_model_classify, max_workers=workers
    ).run(index, paths, reuse_cache=not classify_fresh, progress=progress)
    if classification.failed:
        write_json(paths.manifest_json, manifest)
        raise ClassificationError(classification.failed)
    manifest.record(
        "classify",
        prompt_version=CHARGE_CLASSIFICATION.version,
        prompt_sha=CHARGE_CLASSIFICATION.sha,
        model=settings.gemini_model_classify,
    )

    # Step 4: the document profile and the registry row.
    profile_fresh = force or "profile" in stale_steps
    if profile_fresh:
        paths.profile_json.unlink(missing_ok=True)
    profile = DocumentProfiler(client, model=settings.gemini_model_extract).run(
        paths, page_count=len(pages), reuse_cache=not profile_fresh
    )
    manifest.record(
        "profile",
        prompt_version=DOCUMENT_PROFILE.version,
        prompt_sha=DOCUMENT_PROFILE.sha,
        model=settings.gemini_model_extract,
    )
    row = build_registry_row(
        document_hash=document_hash,
        source=pdf_path.name,
        profile=profile,
        ports_mentioned=classification.ports,
        page_count=len(pages),
    )
    upsert_row(settings.data_dir, row)
    write_json(paths.manifest_json, manifest)

    return IngestResult(
        document_hash=document_hash,
        paths=paths,
        row=row,
        page_count=len(pages),
        section_count=len(index_file.sections),
        classified=len(classification.classified) + len(classification.reused),
        skipped=len(classification.skipped),
        charge_count=len(classification.charges),
        findings=run_sanity_checks(
            index_file.sections,
            classification.charges,
            page_count=len(pages),
            pages_seen=index_report.pages_seen,
        ),
    )
