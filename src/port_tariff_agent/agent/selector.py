"""Which of the document's charges this vessel call has to pay.

One call over the whole catalog rather than one call per charge: the decisions are not
independent, since a charge that applies often rules another one out.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from ..llm.protocol import StructuredGenerator
from ..models import Charge, SectionNode
from ..prompts import CHARGE_SELECTION
from ..tariff_index import content_text

log = logging.getLogger(__name__)

STEP = "select"
PREVIEW_CHARS = 240


class Selection(BaseModel):
    section_id: str = Field(description="The id of a section from the lists given to you")
    reason: str = Field(description="Why it applies to this call, in one sentence")


class SelectorResponse(BaseModel):
    applicable: list[Selection] = Field(
        default_factory=list, description="The charges this vessel call has to pay"
    )
    context_sections: list[Selection] = Field(
        default_factory=list,
        description="Sections that state terms needed to interpret those charges",
    )


def _preview(node: SectionNode) -> str:
    text = content_text(node.text)
    return text[:PREVIEW_CHARS] + "..." if len(text) > PREVIEW_CHARS else text


def render_catalog(charges: Sequence[Charge]) -> str:
    return "\n".join(
        f"- {charge.section_id} | {charge.name}"
        f" | payer: {charge.payer.value if charge.payer else 'unknown'}"
        f" | applies when: {charge.applies_when or 'not stated'}"
        for charge in charges
    )


def render_sections(sections: Sequence[SectionNode]) -> str:
    return "\n".join(f"- {node.id} | {node.title} | {_preview(node)}" for node in sections)


def build_prompt(
    *,
    charges: Sequence[Charge],
    sections: Sequence[SectionNode],
    port: str,
    vessel_description: str,
) -> str:
    return (
        f"{CHARGE_SELECTION.text}\n\n"
        f"## Port\n\n{port}\n\n"
        f"## The vessel call\n\n{vessel_description}\n\n"
        f"## Charges defined by this document\n\n{render_catalog(charges)}\n\n"
        f"## Other sections of this document\n\n{render_sections(sections)}\n"
    )


def _dedupe(selections: Iterable[Selection], *, known: set[str], seen: set[str]) -> list[Selection]:
    kept: list[Selection] = []
    for selection in selections:
        section_id = selection.section_id.strip()
        if section_id not in known or section_id in seen:
            log.debug("dropping selection %r", section_id)
            continue
        seen.add(section_id)
        kept.append(Selection(section_id=section_id, reason=selection.reason))
    return kept


class ChargeSelector:
    def __init__(self, client: StructuredGenerator, *, model: str) -> None:
        self._client = client
        self._model = model

    def select(
        self,
        *,
        charges: Sequence[Charge],
        sections: Sequence[SectionNode],
        port: str,
        vessel_description: str,
    ) -> SelectorResponse:
        """The applicable charges and the context sections, both checked against the document.

        Ids the model invents, or repeats, are dropped: the tool can only return text for a
        section the index actually holds.
        """
        result = self._client.generate_structured(
            call_id=f"{STEP}/{port}",
            model=self._model,
            schema=SelectorResponse,
            prompt=build_prompt(
                charges=charges,
                sections=sections,
                port=port,
                vessel_description=vessel_description,
            ),
        )
        charge_ids = {charge.section_id for charge in charges}
        section_ids = {node.id for node in sections}
        seen: set[str] = set()
        return SelectorResponse(
            applicable=_dedupe(result.value.applicable, known=charge_ids, seen=seen),
            context_sections=_dedupe(
                result.value.context_sections, known=charge_ids | section_ids, seen=seen
            ),
        )
