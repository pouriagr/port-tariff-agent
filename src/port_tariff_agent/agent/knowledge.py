"""What the `get_charges` tool returns: the document, the applicable charges and their text.

Document selection and section retrieval are plain code; only the choice of which charges
apply is a model call.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..models import Charge, ChargesFile, DocumentRow, SectionNode
from ..paths import DocumentPaths
from ..registry import known_ports, load_registry, select_document
from ..storage import read_json
from ..tariff_index import TariffIndex
from .selector import ChargeSelector, Selection

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Document:
    """One ingested document, loaded from the folder its hash names."""

    row: DocumentRow
    index: TariffIndex
    charges: list[Charge]

    @classmethod
    def load(cls, data_dir: Path, row: DocumentRow) -> Document:
        paths = DocumentPaths(data_dir, row.document_hash)
        charges = ChargesFile.model_validate(read_json(paths.charges_json))
        return cls(row=row, index=TariffIndex.load(paths.index_json), charges=charges.charges)

    def summary(self) -> dict[str, Any]:
        return {
            "document_hash": self.row.document_hash,
            "issuer": self.row.issuer,
            "title": self.row.title,
            "currency": self.row.currency,
            "valid_from": str(self.row.valid_from) if self.row.valid_from else None,
            "valid_to": str(self.row.valid_to) if self.row.valid_to else None,
        }

    def context_candidates(self) -> list[SectionNode]:
        """Numbered sections that define no charge but say something of their own.

        General terms - working hours, surcharges, how tonnage is measured - live in
        sections like these, and no ancestor link leads to them (ADR-019).
        """
        charged = {charge.section_id for charge in self.charges}
        return [
            node for node in self.index.sections if node.id not in charged and node.text.strip()
        ]


def _entry(index: TariffIndex, selection: Selection, names: dict[str, str]) -> dict[str, Any]:
    node = index.get_node(selection.section_id)
    return {
        "section_id": selection.section_id,
        "name": names.get(selection.section_id) or (node.title if node else selection.section_id),
        "page_citation": index.page_citation(selection.section_id),
        "reason": selection.reason,
        "text": index.get_context(selection.section_id),
    }


def get_charges(
    *,
    data_dir: Path,
    selector: ChargeSelector,
    port: str,
    vessel_description: str,
    arrival_date: date,
) -> dict[str, Any]:
    """The applicable sections of the document that covers this port on this date."""
    rows = load_registry(data_dir)
    row = select_document(rows, port=port, on=arrival_date)
    if row is None:
        return {
            "error": f"No tariff document covers port {port} on {arrival_date}",
            "known_ports": known_ports(rows),
        }

    document = Document.load(data_dir, row)
    selection = selector.select(
        charges=document.charges,
        sections=document.context_candidates(),
        port=port,
        vessel_description=vessel_description,
    )
    names = {charge.section_id: charge.name for charge in document.charges}
    applicable_ids = {item.section_id for item in selection.applicable}
    log.info("selected %d charges for %s", len(applicable_ids), port)

    return {
        "document": document.summary(),
        "port": port,
        "applicable": [_entry(document.index, item, names) for item in selection.applicable],
        "context": [_entry(document.index, item, names) for item in selection.context_sections],
        "not_applicable": _not_applicable(document.charges, applicable_ids),
    }


def _not_applicable(charges: Sequence[Charge], applicable: set[str]) -> list[dict[str, str]]:
    return [
        {"section_id": charge.section_id, "name": charge.name}
        for charge in charges
        if charge.section_id not in applicable
    ]
