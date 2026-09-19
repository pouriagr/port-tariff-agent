"""The tape machinery: what survives a round trip, and what must never replay silently.

All of it runs against hand-built tapes in `tmp_path`. The real recordings are exercised by
`test_ground_truth.py`; here the point is the failure modes, which a real tape should never
reach.
"""

from __future__ import annotations

import json
import warnings
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from port_tariff_agent.llm.protocol import ModelTurn, ToolCall, UserMessage
from port_tariff_agent.prompts import CHARGE_SELECTION, TARIFF_AGENT
from port_tariff_agent.storage import read_json
from tests.cassettes import (
    CASSETTE_VERSION,
    Cassette,
    CassetteError,
    CassetteStale,
    PromptStamp,
    RecordingClient,
    ReplayingClient,
    StructuredInteraction,
    ToolsInteraction,
    structured_input_sha,
    tools_input_sha,
)

SIGNATURE = b"opaque-thinking-token"
QUERY = "What does this vessel pay?"
TODAY = date(2026, 1, 2)
HASH = "0f1e2d3c4b5a"


class Reply(BaseModel):
    verdict: str


def tools_entry(call_id: str = "agent/0", **turn_args: Any) -> ToolsInteraction:
    defaults: dict[str, Any] = {
        "text": "",
        "tool_calls": (ToolCall(name="do_thing", args={"x": 1}, signature=SIGNATURE),),
    }
    return ToolsInteraction(
        call_id=call_id, input_sha="aaaaaaaaaaaa", turn=ModelTurn(**{**defaults, **turn_args})
    )


def structured_entry(call_id: str = "select/Northaven", **overrides: Any) -> StructuredInteraction:
    defaults: dict[str, Any] = {
        "input_sha": "bbbbbbbbbbbb",
        "schema": "Reply",
        "value": {"verdict": "yes"},
        "model": "model-extract",
    }
    return StructuredInteraction(call_id=call_id, **{**defaults, **overrides})


def cassette(*interactions: Any, **overrides: Any) -> Cassette:
    defaults: dict[str, Any] = {
        "name": "tape",
        "recorded_at": "2026-01-02T10:00:00+02:00",
        "document_hash": HASH,
        "query": QUERY,
        "today": TODAY,
        "models": {"agent": "model-agent", "extract": "model-extract"},
        "prompts": {
            "tariff_agent": PromptStamp.of(TARIFF_AGENT),
            "charge_selection": PromptStamp.of(CHARGE_SELECTION),
        },
    }
    return Cassette(interactions=tuple(interactions), **{**defaults, **overrides})


def replay(*interactions: Any) -> ReplayingClient:
    return ReplayingClient(cassette(*interactions))


def ask_for_tools(client: ReplayingClient, call_id: str = "agent/0") -> ModelTurn:
    return client.generate_with_tools(
        call_id=call_id,
        model="model-agent",
        system_instruction="be useful",
        history=[UserMessage(text=QUERY)],
        tools=[],
    )


def ask_for_structured(
    client: ReplayingClient, call_id: str = "select/Northaven", schema: type[BaseModel] = Reply
) -> Any:
    return client.generate_structured(
        call_id=call_id, model="model-extract", schema=schema, prompt="pick some"
    )


class TestRoundTrip:
    def test_a_signature_survives_being_written_and_read(self, tmp_path: Path) -> None:
        """Base64 in, bytes out. A tape that loses these cannot be replayed against the API."""
        cassette(tools_entry()).save(directory=tmp_path)

        loaded = Cassette.parse(read_json(tmp_path / "tape.json"))

        assert loaded.interactions[0].turn.tool_calls[0].signature == SIGNATURE

    def test_a_turn_level_signature_survives_too(self, tmp_path: Path) -> None:
        cassette(tools_entry(text="thinking", signature=SIGNATURE)).save(directory=tmp_path)

        loaded = Cassette.parse(read_json(tmp_path / "tape.json"))

        assert loaded.interactions[0].turn.signature == SIGNATURE

    def test_a_missing_signature_stays_missing(self, tmp_path: Path) -> None:
        cassette(tools_entry(tool_calls=(ToolCall(name="do_thing", args={}),))).save(
            directory=tmp_path
        )

        loaded = Cassette.parse(read_json(tmp_path / "tape.json"))

        assert loaded.interactions[0].turn.tool_calls[0].signature is None
        assert loaded.interactions[0].turn.signature is None

    def test_both_kinds_of_interaction_keep_their_order(self, tmp_path: Path) -> None:
        cassette(tools_entry(), structured_entry(), tools_entry("agent/1")).save(directory=tmp_path)

        loaded = Cassette.parse(read_json(tmp_path / "tape.json"))

        assert [item.call_id for item in loaded.interactions] == [
            "agent/0",
            "select/Northaven",
            "agent/1",
        ]
        assert [item.kind for item in loaded.interactions] == ["tools", "structured", "tools"]

    def test_the_metadata_round_trips(self, tmp_path: Path) -> None:
        cassette(tools_entry()).save(directory=tmp_path)

        loaded = Cassette.parse(read_json(tmp_path / "tape.json"))

        assert loaded.today == TODAY
        assert loaded.query == QUERY
        assert loaded.document_hash == HASH
        assert loaded.models["agent"] == "model-agent"

    def test_the_file_is_readable_json_with_a_version(self, tmp_path: Path) -> None:
        path = cassette(tools_entry()).save(directory=tmp_path)

        raw = json.loads(path.read_text(encoding="utf-8"))

        assert raw["version"] == CASSETTE_VERSION
        assert raw["interactions"][0]["turn"]["tool_calls"][0]["args"] == {"x": 1}


class TestReplay:
    def test_interactions_are_served_in_order(self) -> None:
        client = replay(tools_entry(), structured_entry(), tools_entry("agent/1"))

        first = ask_for_tools(client)
        picked = ask_for_structured(client)
        ask_for_tools(client, "agent/1")

        assert first.tool_calls[0].name == "do_thing"
        assert picked.value.verdict == "yes"
        client.assert_exhausted()

    def test_running_past_the_end_says_so(self) -> None:
        client = replay(tools_entry())
        ask_for_tools(client)

        with pytest.raises(CassetteError, match="holds 1 interactions"):
            ask_for_tools(client, "agent/1")

    def test_stopping_early_leaves_the_tape_unplayed(self) -> None:
        client = replay(tools_entry(), tools_entry("agent/1"))
        ask_for_tools(client)

        with pytest.raises(CassetteError, match="unplayed interactions: agent/1"):
            client.assert_exhausted()

    def test_a_different_call_id_is_a_different_conversation(self) -> None:
        client = replay(tools_entry("agent/0"))

        with pytest.raises(CassetteError, match="recorded 'agent/0'"):
            ask_for_tools(client, "agent/7")

    def test_the_two_protocols_may_not_be_swapped(self) -> None:
        client = replay(structured_entry())

        with pytest.raises(CassetteError, match="recorded a structured call"):
            ask_for_tools(client, "select/Northaven")

    def test_a_different_schema_is_refused(self) -> None:
        class Other(BaseModel):
            verdict: str

        client = replay(structured_entry())

        with pytest.raises(CassetteError, match="recorded schema 'Reply'"):
            ask_for_structured(client, schema=Other)

    def test_the_recorded_value_is_validated_not_trusted(self) -> None:
        client = replay(structured_entry(value={"wrong_field": 1}))

        with pytest.raises(ValueError, match="verdict"):
            ask_for_structured(client)

    def test_an_unknown_version_will_not_load(self) -> None:
        with pytest.raises(CassetteError, match="cassette version 99"):
            Cassette.parse({"version": 99})

    def test_an_unknown_kind_will_not_load(self) -> None:
        raw = cassette(tools_entry()).as_json()
        raw["interactions"][0]["kind"] = "telepathy"

        with pytest.raises(CassetteError, match="unknown interaction kind"):
            Cassette.parse(raw)


class TestDrift:
    def test_a_changed_prompt_is_reported_but_still_replays(self, tmp_path: Path) -> None:
        stamps = {
            "tariff_agent": PromptStamp(version=1, sha="deadbeefcafe"),
            "charge_selection": PromptStamp.of(CHARGE_SELECTION),
        }
        cassette(tools_entry(), prompts=stamps).save(directory=tmp_path)

        with pytest.warns(CassetteStale, match="tariff_agent v1 sha deadbeefcafe"):
            loaded = Cassette.load("tape", directory=tmp_path)

        assert loaded.interactions  # the tape is usable; the warning is the whole response

    def test_an_unchanged_prompt_reports_nothing(self, tmp_path: Path) -> None:
        cassette(tools_entry()).save(directory=tmp_path)

        with warnings.catch_warnings():
            warnings.simplefilter("error", CassetteStale)
            loaded = Cassette.load("tape", directory=tmp_path)

        assert loaded.prompt_drift() == []

    def test_a_changed_input_is_noted_without_failing(self) -> None:
        client = replay(tools_entry())

        turn = ask_for_tools(client)

        assert turn.tool_calls[0].name == "do_thing"  # the drift did not stop the replay
        assert client.input_drift == [f"agent/0: input sha aaaaaaaaaaaa -> {_expected_sha()}"]

    def test_an_unchanged_input_is_not_noted(self) -> None:
        client = replay(structured_entry(input_sha=structured_input_sha("pick some")))

        ask_for_structured(client)

        assert client.input_drift == []

    def test_the_input_digest_follows_what_the_model_was_shown(self) -> None:
        history = [UserMessage(text="go")]

        assert tools_input_sha("be useful", history) != tools_input_sha("be brief", history)
        assert tools_input_sha("be useful", history) != tools_input_sha("be useful", [])
        assert tools_input_sha("be useful", history) == tools_input_sha("be useful", list(history))


def _expected_sha() -> str:
    return tools_input_sha("be useful", [UserMessage(text=QUERY)])


class TestRecording:
    def test_what_the_inner_client_returned_is_what_gets_taped(self) -> None:
        turn = ModelTurn(
            text="thinking",
            tool_calls=(ToolCall(name="do_thing", args={"x": 1}, signature=SIGNATURE),),
            signature=SIGNATURE,
            model="model-agent",
            prompt_tokens=11,
            output_tokens=3,
        )
        recorder = RecordingClient(
            _InnerStub(turn, Reply(verdict="yes")),
            name="tape",
            query=QUERY,
            today=TODAY,
            document_hash=HASH,
            models={"agent": "model-agent", "extract": "model-extract"},
        )

        recorder.generate_with_tools(
            call_id="agent/0",
            model="model-agent",
            system_instruction="be useful",
            history=[UserMessage(text=QUERY)],
            tools=[],
        )
        recorder.generate_structured(
            call_id="select/Northaven", model="model-extract", schema=Reply, prompt="pick some"
        )
        taped = recorder.finish()

        assert [item.call_id for item in taped.interactions] == ["agent/0", "select/Northaven"]
        assert taped.interactions[0].turn == turn
        assert taped.interactions[1].value == {"verdict": "yes"}
        assert taped.token_totals == (11 + 5, 3 + 2)  # both call sites count

    def test_a_recording_stamps_the_prompts_it_ran_under(self) -> None:
        recorder = RecordingClient(
            _InnerStub(ModelTurn(), Reply(verdict="yes")),
            name="tape",
            query=QUERY,
            today=TODAY,
            document_hash=HASH,
            models={},
        )

        taped = recorder.finish()

        assert taped.prompts["tariff_agent"] == PromptStamp.of(TARIFF_AGENT)
        assert taped.prompt_drift() == []

    def test_a_recorded_tape_replays(self, tmp_path: Path) -> None:
        """The loop that matters: record, save, load, replay, same turn."""
        call = ToolCall(name="do_thing", args={"x": 1}, signature=SIGNATURE)
        turn = ModelTurn(tool_calls=(call,))
        recorder = RecordingClient(
            _InnerStub(turn, Reply(verdict="yes")),
            name="tape",
            query=QUERY,
            today=TODAY,
            document_hash=HASH,
            models={"agent": "model-agent"},
        )
        recorder.generate_with_tools(
            call_id="agent/0",
            model="model-agent",
            system_instruction="be useful",
            history=[UserMessage(text=QUERY)],
            tools=[],
        )
        recorder.finish().save(directory=tmp_path)

        client = ReplayingClient(Cassette.load("tape", directory=tmp_path))
        replayed = ask_for_tools(client)

        assert replayed == turn
        assert client.input_drift == []
        client.assert_exhausted()


class _InnerStub:
    def __init__(self, turn: ModelTurn, value: BaseModel) -> None:
        self._turn = turn
        self._value = value

    def generate_with_tools(self, **_kwargs: Any) -> ModelTurn:
        return self._turn

    def generate_structured(self, **_kwargs: Any) -> Any:
        from port_tariff_agent.llm.protocol import LlmResult

        return LlmResult(value=self._value, model="model-extract", prompt_tokens=5, output_tokens=2)
