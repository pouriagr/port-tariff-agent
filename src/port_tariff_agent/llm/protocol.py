"""The boundary between this project and whatever model provider sits behind it.

No provider type crosses this line, so the steps can be tested without a network and a
different provider would touch one module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

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


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool offered to the model: a name, what it does, and its argument schema."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: Mapping[str, Any]
    signature: bytes | None = None
    """Opaque token a provider may attach to a call and require back with the result."""


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelTurn:
    """One reply: prose, tool calls, or both."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    signature: bytes | None = None
    model: str = ""
    prompt_tokens: int | None = None
    output_tokens: int | None = None


Message = UserMessage | ModelTurn | ToolResult


class ToolCallingGenerator(Protocol):
    """A conversation the model can drive by calling tools."""

    def generate_with_tools(
        self,
        *,
        call_id: str,
        model: str,
        system_instruction: str,
        history: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn: ...


class LlmClient(StructuredGenerator, ToolCallingGenerator, Protocol):
    """Both capabilities in one object, which is how the agent is composed.

    The loop drives the conversation and the charge selector asks its one structured
    question; they share a client, so anything standing in for the provider has to answer
    both.
    """
