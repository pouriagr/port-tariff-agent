"""Recording and replaying a whole agent conversation (ADR-023).

The loop is what is being validated, so a fixture that froze one response per call would
prove nothing: the tool results, the section texts and every amount are produced by real
code from the committed artifacts. A cassette therefore records only what the *model* said,
in order, and replay recomputes the rest. Given the same tape and the same document, a run
reproduces exactly.

Both of the agent's model call sites are served from one ordered queue, because they
interleave — `agent/0` asks for charges, `select/<port>` answers from inside that tool call,
`agent/1` continues. Keying by `call_id` would lose that order and would collide anyway:
`select/<port>` repeats if a port is asked about twice, and `agent/<step>` restarts at zero
on a follow-up question. The recorded `call_id` is kept as an assertion instead.
"""

from __future__ import annotations

import base64
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from port_tariff_agent.llm.protocol import (
    InlineFile,
    LlmResult,
    Message,
    ModelTurn,
    ToolCall,
    ToolSpec,
)
from port_tariff_agent.prompts import CHARGE_SELECTION, TARIFF_AGENT, Prompt
from port_tariff_agent.storage import read_json, sha256_text, write_json

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE_VERSION = 1
SHA_CHARS = 12

TOOLS = "tools"
STRUCTURED = "structured"

STAMPED_PROMPTS: Mapping[str, Prompt] = {
    "tariff_agent": TARIFF_AGENT,
    "charge_selection": CHARGE_SELECTION,
}


class CassetteError(AssertionError):
    """The tape no longer describes this run. Always fatal; re-record."""


class CassetteStale(UserWarning):
    """The tape still replays, but it was recorded under different instructions."""


@dataclass(frozen=True, slots=True)
class PromptStamp:
    version: int
    sha: str

    @classmethod
    def of(cls, prompt: Prompt) -> PromptStamp:
        return cls(version=prompt.version, sha=prompt.sha)


@dataclass(frozen=True, slots=True)
class ToolsInteraction:
    """One `generate_with_tools` reply."""

    call_id: str
    input_sha: str
    turn: ModelTurn

    kind = TOOLS


@dataclass(frozen=True, slots=True)
class StructuredInteraction:
    """One `generate_structured` reply, as the model produced it.

    The value is stored raw rather than post-processed, so replay exercises the validation
    and de-duplication the production code does on the way out.
    """

    call_id: str
    input_sha: str
    schema: str
    value: dict[str, Any]
    model: str = ""
    prompt_tokens: int | None = None
    output_tokens: int | None = None

    kind = STRUCTURED


Interaction = ToolsInteraction | StructuredInteraction


def _encode(signature: bytes | None) -> str | None:
    return None if signature is None else base64.b64encode(signature).decode("ascii")


def _decode(raw: str | None) -> bytes | None:
    return None if raw is None else base64.b64decode(raw)


def tools_input_sha(system_instruction: str, history: Sequence[Message]) -> str:
    """What the model was shown. The messages are frozen dataclasses, so `repr` is total."""
    joined = "\n".join([system_instruction, *(repr(message) for message in history)])
    return sha256_text(joined)[:SHA_CHARS]


def structured_input_sha(prompt: str) -> str:
    return sha256_text(prompt)[:SHA_CHARS]


@dataclass(frozen=True, slots=True)
class Cassette:
    name: str
    recorded_at: str
    document_hash: str
    query: str
    today: date
    models: Mapping[str, str]
    prompts: Mapping[str, PromptStamp]
    interactions: tuple[Interaction, ...]

    @classmethod
    def load(cls, name: str, *, directory: Path = CASSETTE_DIR) -> Cassette:
        cassette = cls.parse(read_json(directory / f"{name}.json"))
        if drift := cassette.prompt_drift():
            warnings.warn(
                f"cassette {cassette.name!r} was recorded with {'; '.join(drift)}."
                " It still replays; re-record with --live to make the claim current.",
                CassetteStale,
                stacklevel=2,
            )
        return cassette

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> Cassette:
        version = raw.get("version")
        if version != CASSETTE_VERSION:
            raise CassetteError(f"cassette version {version!r}, expected {CASSETTE_VERSION}")
        return cls(
            name=raw["name"],
            recorded_at=raw["recorded_at"],
            document_hash=raw["document_hash"],
            query=raw["query"],
            today=date.fromisoformat(raw["today"]),
            models=dict(raw["models"]),
            prompts={
                name: PromptStamp(version=stamp["version"], sha=stamp["sha"])
                for name, stamp in raw["prompts"].items()
            },
            interactions=tuple(_parse_interaction(item) for item in raw["interactions"]),
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "version": CASSETTE_VERSION,
            "name": self.name,
            "recorded_at": self.recorded_at,
            "document_hash": self.document_hash,
            "query": self.query,
            "today": self.today.isoformat(),
            "models": dict(self.models),
            "prompts": {
                name: {"version": stamp.version, "sha": stamp.sha}
                for name, stamp in self.prompts.items()
            },
            "interactions": [_dump_interaction(item) for item in self.interactions],
        }

    def save(self, *, directory: Path = CASSETTE_DIR) -> Path:
        path = directory / f"{self.name}.json"
        write_json(path, self.as_json())
        return path

    def prompt_drift(self) -> list[str]:
        """Prompts whose text has moved since the recording. Reported, never fatal."""
        drift = []
        for name, stamp in sorted(self.prompts.items()):
            current = STAMPED_PROMPTS.get(name)
            if current is None:
                continue
            if (current.version, current.sha) != (stamp.version, stamp.sha):
                drift.append(
                    f"{name} v{stamp.version} sha {stamp.sha},"
                    f" now v{current.version} sha {current.sha}"
                )
        return drift

    @property
    def token_totals(self) -> tuple[int, int]:
        prompt = sum(_tokens(item)[0] for item in self.interactions)
        output = sum(_tokens(item)[1] for item in self.interactions)
        return prompt, output


def _tokens(interaction: Interaction) -> tuple[int, int]:
    source = interaction.turn if isinstance(interaction, ToolsInteraction) else interaction
    return source.prompt_tokens or 0, source.output_tokens or 0


def _parse_interaction(raw: Mapping[str, Any]) -> Interaction:
    kind = raw.get("kind")
    if kind == TOOLS:
        turn = raw["turn"]
        return ToolsInteraction(
            call_id=raw["call_id"],
            input_sha=raw["input_sha"],
            turn=ModelTurn(
                text=turn.get("text", ""),
                tool_calls=tuple(
                    ToolCall(
                        name=call["name"],
                        args=call.get("args", {}),
                        signature=_decode(call.get("signature_b64")),
                    )
                    for call in turn.get("tool_calls", ())
                ),
                signature=_decode(turn.get("signature_b64")),
                model=raw.get("model", ""),
                prompt_tokens=raw.get("prompt_tokens"),
                output_tokens=raw.get("output_tokens"),
            ),
        )
    if kind == STRUCTURED:
        return StructuredInteraction(
            call_id=raw["call_id"],
            input_sha=raw["input_sha"],
            schema=raw["schema"],
            value=dict(raw["value"]),
            model=raw.get("model", ""),
            prompt_tokens=raw.get("prompt_tokens"),
            output_tokens=raw.get("output_tokens"),
        )
    raise CassetteError(f"unknown interaction kind {kind!r}")


def _dump_interaction(interaction: Interaction) -> dict[str, Any]:
    if isinstance(interaction, ToolsInteraction):
        turn = interaction.turn
        return {
            "kind": TOOLS,
            "call_id": interaction.call_id,
            "input_sha": interaction.input_sha,
            "model": turn.model,
            "prompt_tokens": turn.prompt_tokens,
            "output_tokens": turn.output_tokens,
            "turn": {
                "text": turn.text,
                "signature_b64": _encode(turn.signature),
                "tool_calls": [
                    {
                        "name": call.name,
                        "args": dict(call.args),
                        "signature_b64": _encode(call.signature),
                    }
                    for call in turn.tool_calls
                ],
            },
        }
    return {
        "kind": STRUCTURED,
        "call_id": interaction.call_id,
        "input_sha": interaction.input_sha,
        "schema": interaction.schema,
        "model": interaction.model,
        "prompt_tokens": interaction.prompt_tokens,
        "output_tokens": interaction.output_tokens,
        "value": interaction.value,
    }


class ReplayingClient:
    """A drop-in for `GeminiClient` that answers from a tape instead of the network.

    Serves both protocols, because the agent is composed from one client object.
    """

    def __init__(self, cassette: Cassette) -> None:
        self._cassette = cassette
        self._remaining = list(cassette.interactions)
        self._served = 0
        self.input_drift: list[str] = []

    def generate_structured[T: BaseModel](
        self,
        *,
        call_id: str,
        model: str,
        schema: type[T],
        prompt: str,
        files: Sequence[InlineFile] = (),
        max_output_tokens: int | None = None,
    ) -> LlmResult[T]:
        interaction = self._next(STRUCTURED, call_id)
        assert isinstance(interaction, StructuredInteraction)
        if interaction.schema != schema.__name__:
            raise CassetteError(
                f"{self._where(call_id)}: recorded schema {interaction.schema!r},"
                f" the run asked for {schema.__name__!r}"
            )
        self._note_drift(call_id, interaction.input_sha, structured_input_sha(prompt))
        return LlmResult(
            value=schema.model_validate(interaction.value),
            model=interaction.model or model,
            prompt_tokens=interaction.prompt_tokens,
            output_tokens=interaction.output_tokens,
        )

    def generate_with_tools(
        self,
        *,
        call_id: str,
        model: str,
        system_instruction: str,
        history: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        interaction = self._next(TOOLS, call_id)
        assert isinstance(interaction, ToolsInteraction)
        self._note_drift(
            call_id, interaction.input_sha, tools_input_sha(system_instruction, history)
        )
        return interaction.turn

    def assert_exhausted(self) -> None:
        if self._remaining:
            left = ", ".join(item.call_id for item in self._remaining)
            raise CassetteError(
                f"cassette {self._cassette.name!r} has unplayed interactions: {left}."
                " The run stopped earlier than the recording did; re-record."
            )

    def _next(self, kind: str, call_id: str) -> Interaction:
        if not self._remaining:
            raise CassetteError(
                f"cassette {self._cassette.name!r} holds {self._served} interactions;"
                f" the run asked for another ({call_id}). Re-record with --live."
            )
        interaction = self._remaining.pop(0)
        self._served += 1
        if interaction.kind != kind:
            raise CassetteError(
                f"{self._where(call_id)}: recorded a {interaction.kind} call,"
                f" the run made a {kind} one"
            )
        if interaction.call_id != call_id:
            raise CassetteError(
                f"{self._where(call_id)}: recorded {interaction.call_id!r},"
                f" the run asked for {call_id!r}. The conversation took a different path."
            )
        return interaction

    def _where(self, call_id: str) -> str:
        return f"cassette {self._cassette.name!r} at interaction {self._served} ({call_id})"

    def _note_drift(self, call_id: str, recorded: str, current: str) -> None:
        if recorded != current:
            self.input_drift.append(f"{call_id}: input sha {recorded} -> {current}")


class RecordingClient:
    """Passes every call to a real client and keeps what came back."""

    def __init__(
        self,
        inner: Any,
        *,
        name: str,
        query: str,
        today: date,
        document_hash: str,
        models: Mapping[str, str],
    ) -> None:
        self._inner = inner
        self._name = name
        self._query = query
        self._today = today
        self._document_hash = document_hash
        self._models = dict(models)
        self._interactions: list[Interaction] = []

    def generate_structured[T: BaseModel](
        self,
        *,
        call_id: str,
        model: str,
        schema: type[T],
        prompt: str,
        files: Sequence[InlineFile] = (),
        max_output_tokens: int | None = None,
    ) -> LlmResult[T]:
        result = self._inner.generate_structured(
            call_id=call_id,
            model=model,
            schema=schema,
            prompt=prompt,
            files=files,
            max_output_tokens=max_output_tokens,
        )
        self._interactions.append(
            StructuredInteraction(
                call_id=call_id,
                input_sha=structured_input_sha(prompt),
                schema=schema.__name__,
                value=result.value.model_dump(mode="json"),
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                output_tokens=result.output_tokens,
            )
        )
        return result

    def generate_with_tools(
        self,
        *,
        call_id: str,
        model: str,
        system_instruction: str,
        history: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> ModelTurn:
        input_sha = tools_input_sha(system_instruction, history)
        turn = self._inner.generate_with_tools(
            call_id=call_id,
            model=model,
            system_instruction=system_instruction,
            history=history,
            tools=tools,
        )
        self._interactions.append(ToolsInteraction(call_id=call_id, input_sha=input_sha, turn=turn))
        return turn

    def finish(self) -> Cassette:
        return Cassette(
            name=self._name,
            recorded_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            document_hash=self._document_hash,
            query=self._query,
            today=self._today,
            models=self._models,
            prompts={name: PromptStamp.of(prompt) for name, prompt in STAMPED_PROMPTS.items()},
            interactions=tuple(self._interactions),
        )
