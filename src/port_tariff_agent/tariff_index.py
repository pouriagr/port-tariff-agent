"""Reading the section index.

The query phase imports this and nothing from `ingestion`: the write side builds the file,
this side answers questions about it.
"""

from __future__ import annotations

from pathlib import Path

from .errors import PortTariffError
from .models import SectionNode, TariffIndexFile
from .storage import read_json

MAX_HEADING_LEVEL = 6


class SectionNotFoundError(PortTariffError):
    def __init__(self, section_id: str) -> None:
        super().__init__(f"No section {section_id!r} in this document")
        self.section_id = section_id


def render_section(node: SectionNode) -> str:
    hashes = "#" * min(node.depth, MAX_HEADING_LEVEL)
    heading = f"{hashes} {node.id} {node.title}"
    return f"{heading}\n\n{node.text}".rstrip()


class TariffIndex:
    def __init__(self, file: TariffIndexFile) -> None:
        self._file = file
        self._by_id = {node.id: node for node in file.sections}

    @classmethod
    def load(cls, path: Path) -> TariffIndex:
        return cls(TariffIndexFile.model_validate(read_json(path)))

    @property
    def document_hash(self) -> str:
        return self._file.document_hash

    @property
    def sections(self) -> list[SectionNode]:
        return list(self._file.sections)

    def get_node(self, section_id: str) -> SectionNode | None:
        return self._by_id.get(section_id)

    def require(self, section_id: str) -> SectionNode:
        node = self._by_id.get(section_id)
        if node is None:
            raise SectionNotFoundError(section_id)
        return node

    def ancestors(self, section_id: str) -> list[SectionNode]:
        """Root first, excluding the section itself."""
        node = self.require(section_id)
        chain: list[SectionNode] = []
        parent_id = node.parent
        while parent_id is not None:
            parent = self._by_id[parent_id]
            chain.append(parent)
            parent_id = parent.parent
        chain.reverse()
        return chain

    def descendants(self, section_id: str) -> list[SectionNode]:
        """Every node below this one, in document order."""
        self.require(section_id)
        prefix = f"{section_id}."
        return sorted(
            (node for node in self._file.sections if node.id.startswith(prefix)),
            key=lambda node: node.order,
        )

    def get_with_children(self, section_id: str) -> str:
        node = self.require(section_id)
        blocks = [render_section(node)]
        blocks.extend(render_section(child) for child in self.descendants(section_id))
        return "\n\n".join(block for block in blocks if block.strip())

    def get_context(self, section_id: str) -> str:
        """Ancestors' own text, root first, then the section with its children.

        General terms live in parent sections, so they have to travel with the charge.
        Ancestors contribute their own text only; their whole subtrees would be most of
        the document.
        """
        self.require(section_id)
        blocks = [render_section(ancestor) for ancestor in self.ancestors(section_id)]
        blocks.append(self.get_with_children(section_id))
        return "\n\n".join(block for block in blocks if block.strip())

    def page_citation(self, section_id: str) -> str:
        """What an answer cites: the printed page where known, the PDF page otherwise."""
        node = self.require(section_id)
        if node.printed_page is not None:
            return str(node.printed_page)
        if node.pdf_page is not None:
            return f"PDF page {node.pdf_page}"
        return "page unknown"
