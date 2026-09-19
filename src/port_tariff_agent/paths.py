"""Where a document's artifacts live.

Paths are a function of the data directory and the document hash, and are never persisted,
so the data directory can move and a fresh clone still resolves everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REGISTRY_FILENAME = "documents.json"


@dataclass(frozen=True, slots=True)
class DocumentPaths:
    data_dir: Path
    document_hash: str

    @staticmethod
    def registry_file(data_dir: Path) -> Path:
        return data_dir / REGISTRY_FILENAME

    @property
    def dir(self) -> Path:
        return self.data_dir / self.document_hash

    @property
    def source_pdf(self) -> Path:
        return self.dir / "source.pdf"

    @property
    def pages_dir(self) -> Path:
        return self.dir / "pages"

    @property
    def tariff_md(self) -> Path:
        return self.dir / "tariff.md"

    @property
    def index_json(self) -> Path:
        return self.dir / "tariff_index.json"

    @property
    def charges_json(self) -> Path:
        return self.dir / "charges.json"

    @property
    def classifications_jsonl(self) -> Path:
        return self.dir / "classification.jsonl"

    @property
    def profile_json(self) -> Path:
        return self.dir / "profile.json"

    @property
    def manifest_json(self) -> Path:
        return self.dir / "manifest.json"

    def page_md(self, page_number: int) -> Path:
        return self.pages_dir / f"page_{page_number:03d}.md"

    def page_error(self, page_number: int) -> Path:
        return self.pages_dir / f"page_{page_number:03d}.error"

    def ensure(self) -> None:
        self.pages_dir.mkdir(parents=True, exist_ok=True)
