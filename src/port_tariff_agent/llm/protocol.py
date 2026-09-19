"""The boundary between this project and whatever model provider sits behind it.

No provider type crosses this line, so the steps can be tested without a network and a
different provider would touch one module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class InlineFile:
    """A file sent along with the prompt."""

    mime_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class LlmResult[T: BaseModel]:
    value: T
    model: str = ""
    prompt_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """What a step asked for. Recorded by the fake client in tests."""

    call_id: str
    model: str
    prompt: str
    files: Sequence[InlineFile] = field(default_factory=tuple)


class StructuredGenerator(Protocol):
    """Every call returns a validated instance of a fixed schema."""

    def generate_structured[T: BaseModel](
        self,
        *,
        call_id: str,
        model: str,
        schema: type[T],
        prompt: str,
        files: Sequence[InlineFile] = (),
        max_output_tokens: int | None = None,
    ) -> LlmResult[T]: ...
