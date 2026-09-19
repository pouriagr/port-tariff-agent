"""Step 3: decide which sections define a payable charge."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, model_validator

from ..concurrency import map_bounded
from ..llm.protocol import StructuredGenerator
from ..models import Charge, ChargesFile, ClassificationRecord, Payer, SectionNode
from ..paths import DocumentPaths
from ..prompts import CHARGE_CLASSIFICATION
from ..storage import append_jsonl, read_jsonl, sha256_text, write_json
from ..tariff_index import TariffIndex, content_text

log = logging.getLogger(__name__)

STEP = "classify"

ProgressFn = Callable[[str, int, int], None]


class ChargeClassification(BaseModel):
    """What the model is asked for. `section_id` is deliberately absent: it comes from the
    loop, never from the model."""

    defines_charge: bool
    charge_name: str | None = None
    payer: Payer | None = None
    applies_when: str | None = None
    ports_mentioned: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def clear_fields_when_negative(self) -> ChargeClassification:
        if not self.defines_charge:
            self.charge_name = None
            self.payer = None
            self.applies_when = None
        return self


@dataclass
class ClassificationReport:
    classified: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    unnamed: list[str] = field(default_factory=list)


def should_classify(node: SectionNode) -> bool:
    """A leaf is always classified; a container only when it says something itself.

    A short leaf is exactly where a rate table sits, so skipping it on length would lose a
    whole charge (ADR-016).
    """
    if not node.children:
        return True
    return bool(content_text(node.text))


def build_prompt(index: TariffIndex, node: SectionNode) -> str:
    chain = [*index.ancestors(node.id), node]
    heading_path = " > ".join(f"{item.id} {item.title}" for item in chain)
    return f"{CHARGE_CLASSIFICATION.text}\n\nSection path: {heading_path}\n\n{node.text}"


class ChargeClassifier:
    def __init__(self, client: StructuredGenerator, *, model: str, max_workers: int = 4) -> None:
        self._client = client
        self._model = model
        self._max_workers = max_workers
        self._lock = threading.Lock()

    def classify_section(self, index: TariffIndex, node: SectionNode) -> ChargeClassification:
        result = self._client.generate_structured(
            call_id=f"{STEP}/{node.id}",
            model=self._model,
            schema=ChargeClassification,
            prompt=build_prompt(index, node),
        )
        return result.value

    def _cached(self, paths: DocumentPaths) -> dict[str, ClassificationRecord]:
        records: dict[str, ClassificationRecord] = {}
        for row in read_jsonl(paths.classifications_jsonl):
            record = ClassificationRecord.model_validate(row)
            if (
                record.prompt_version == CHARGE_CLASSIFICATION.version
                and record.prompt_sha == CHARGE_CLASSIFICATION.sha
                and record.model == self._model
            ):
                records[record.section_id] = record
        return records

    def run(
        self,
        index: TariffIndex,
        paths: DocumentPaths,
        *,
        reuse_cache: bool = True,
        progress: ProgressFn | None = None,
    ) -> ClassificationReport:
        report = ClassificationReport()
        cache = self._cached(paths) if reuse_cache else {}
        records: dict[str, ClassificationRecord] = {}

        todo: list[SectionNode] = []
        for node in index.sections:
            if not should_classify(node):
                report.skipped.append(node.id)
                continue
            cached = cache.get(node.id)
            if cached is not None and cached.section_text_sha == sha256_text(node.text):
                records[node.id] = cached
                report.reused.append(node.id)
            else:
                todo.append(node)

        done = len(report.reused)
        total = done + len(todo)
        if progress is not None:
            progress(STEP, done, total)

        def work(node: SectionNode) -> ChargeClassification:
            return self.classify_section(index, node)

        def succeeded(node: SectionNode, answer: ChargeClassification) -> None:
            nonlocal done
            record = ClassificationRecord(
                section_id=node.id,
                defines_charge=answer.defines_charge,
                charge_name=answer.charge_name,
                payer=answer.payer,
                applies_when=answer.applies_when,
                ports_mentioned=answer.ports_mentioned,
                section_text_sha=sha256_text(node.text),
                prompt_version=CHARGE_CLASSIFICATION.version,
                prompt_sha=CHARGE_CLASSIFICATION.sha,
                model=self._model,
            )
            with self._lock:
                append_jsonl(paths.classifications_jsonl, record)
            records[node.id] = record
            report.classified.append(node.id)
            done += 1
            if progress is not None:
                progress(STEP, done, total)

        def failed(node: SectionNode, exc: BaseException) -> None:
            nonlocal done
            report.failed.append(node.id)
            log.warning("section %s failed to classify: %s", node.id, exc)
            done += 1
            if progress is not None:
                progress(STEP, done, total)

        map_bounded(
            todo, work, max_workers=self._max_workers, on_success=succeeded, on_failure=failed
        )

        order = {node.id: node.order for node in index.sections}
        for record in sorted(records.values(), key=lambda item: order.get(item.section_id, 0)):
            report.ports.extend(record.ports_mentioned)
            if not record.defines_charge:
                continue
            if not record.charge_name:
                report.unnamed.append(record.section_id)
                continue
            report.charges.append(
                Charge(
                    section_id=record.section_id,
                    name=record.charge_name,
                    payer=record.payer,
                    applies_when=record.applies_when,
                )
            )

        report.classified.sort()
        report.failed.sort()

        if not report.failed:
            write_json(
                paths.charges_json,
                ChargesFile(
                    document_hash=index.document_hash,
                    prompt_version=CHARGE_CLASSIFICATION.version,
                    model=self._model,
                    charges=report.charges,
                ),
            )

        return report
