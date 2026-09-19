"""Prompt texts, kept as files so they can be reviewed and diffed as prose.

Each prompt carries a declared version and the sha of its text. A step's cache is stale
when either changes, so a forgotten version bump cannot silently reuse output produced by
different instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from ..storage import sha256_text


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: int
    text: str

    @property
    def sha(self) -> str:
        return sha256_text(self.text)[:12]


def _load(filename: str, *, version: int) -> Prompt:
    raw = files(__package__).joinpath(filename).read_text(encoding="utf-8")
    return Prompt(
        name=filename.removesuffix(".md"), version=version, text=raw.replace("\r\n", "\n")
    )


PAGE_TRANSCRIPTION = _load("page_transcription.md", version=1)
CHARGE_CLASSIFICATION = _load("charge_classification.md", version=2)
DOCUMENT_PROFILE = _load("document_profile.md", version=1)

ALL_PROMPTS = (PAGE_TRANSCRIPTION, CHARGE_CLASSIFICATION, DOCUMENT_PROFILE)
