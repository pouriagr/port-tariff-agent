"""The ADR-020 boundary: neutral messages out to the SDK, an SDK response back in.

Everything here goes through `GeminiClient`'s public methods with the SDK stubbed, so the
config assembly and both directions of the translation are covered. The signatures matter
more than they look: a thinking model rejects a conversation whose earlier parts lost
theirs, and a recorded cassette that drops one cannot be replayed against the real API.
"""

from __future__ import annotations

from typing import Any

import pytest
from google.genai import types

from port_tariff_agent.errors import LlmError
from port_tariff_agent.llm import client as client_module
from port_tariff_agent.llm.client import GeminiClient
from port_tariff_agent.llm.protocol import (
    ModelTurn,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)
from port_tariff_agent.settings import Settings

SIGNATURE = b"opaque-thinking-token"
OTHER_SIGNATURE = b"a-second-token"


class StubModels:
    """Stands in for `genai.Client().models`, recording what it was asked to generate."""

    def __init__(self, response: types.GenerateContentResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:
        self.calls.append(kwargs)
        return self._response


@pytest.fixture
def stub_sdk(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Any:
    """Build a GeminiClient whose SDK is a stub, and hand back both."""

    def build(response: types.GenerateContentResponse) -> tuple[GeminiClient, StubModels]:
        models = StubModels(response)
        monkeypatch.setattr(
            client_module.genai, "Client", lambda **_kwargs: type("Stub", (), {"models": models})
        )
        return GeminiClient(settings), models

    return build


def turn_response(*parts: types.Part) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))]
    )


def call_with_tools(client: GeminiClient, history: list[Any]) -> ModelTurn:
    return client.generate_with_tools(
        call_id="agent/0",
        model="model-agent",
        system_instruction="be useful",
        history=history,
        tools=[ToolSpec(name="do_thing", description="does it", parameters={"type": "object"})],
    )


def test_a_tool_call_keeps_its_signature_on_the_way_in(stub_sdk: Any) -> None:
    part = types.Part.from_function_call(name="do_thing", args={"x": 1})
    part.thought_signature = SIGNATURE
    client, _models = stub_sdk(turn_response(part))

    turn = call_with_tools(client, [UserMessage(text="go")])

    assert turn.tool_calls == (ToolCall(name="do_thing", args={"x": 1}, signature=SIGNATURE),)


def test_the_turn_keeps_the_signature_of_its_text(stub_sdk: Any) -> None:
    """Without this the signature is read and thrown away, and the tape records a null."""
    part = types.Part.from_text(text="thinking out loud")
    part.thought_signature = SIGNATURE
    client, _models = stub_sdk(turn_response(part))

    turn = call_with_tools(client, [UserMessage(text="go")])

    assert turn.text == "thinking out loud"
    assert turn.signature == SIGNATURE


def test_a_turn_without_signatures_reports_none(stub_sdk: Any) -> None:
    client, _models = stub_sdk(turn_response(types.Part.from_text(text="plain")))

    turn = call_with_tools(client, [UserMessage(text="go")])

    assert turn.signature is None
    assert turn.tool_calls == ()


def test_every_signature_in_the_history_is_sent_back(stub_sdk: Any) -> None:
    client, models = stub_sdk(turn_response(types.Part.from_text(text="done")))
    history = [
        UserMessage(text="go"),
        ModelTurn(
            text="thinking",
            tool_calls=(ToolCall(name="do_thing", args={"x": 1}, signature=SIGNATURE),),
            signature=OTHER_SIGNATURE,
        ),
        ToolResult(name="do_thing", payload={"ok": True}),
    ]

    call_with_tools(client, history)

    contents = models.calls[0]["contents"]
    model_turn = contents[1]
    assert model_turn.role == "model"
    assert [part.thought_signature for part in model_turn.parts] == [OTHER_SIGNATURE, SIGNATURE]


def test_each_kind_of_message_becomes_the_right_content(stub_sdk: Any) -> None:
    client, models = stub_sdk(turn_response(types.Part.from_text(text="done")))
    history = [
        UserMessage(text="go"),
        ModelTurn(tool_calls=(ToolCall(name="do_thing", args={"x": 1}),)),
        ToolResult(name="do_thing", payload={"ok": True}),
    ]

    call_with_tools(client, history)

    contents = models.calls[0]["contents"]
    assert [content.role for content in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "go"
    assert contents[1].parts[0].function_call.name == "do_thing"
    assert contents[2].parts[0].function_response.name == "do_thing"


def test_the_loop_dispatches_its_own_tools(stub_sdk: Any) -> None:
    """Automatic function calling would run the tools behind the loop's back."""
    client, models = stub_sdk(turn_response(types.Part.from_text(text="done")))

    call_with_tools(client, [UserMessage(text="go")])

    config = models.calls[0]["config"]
    assert config.automatic_function_calling.disable is True
    assert config.system_instruction == "be useful"
    assert [declaration.name for declaration in config.tools[0].function_declarations] == [
        "do_thing"
    ]


def test_a_response_cut_off_at_the_token_limit_is_retryable(stub_sdk: Any) -> None:
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text="half")]),
                finish_reason=types.FinishReason.MAX_TOKENS,
            )
        ]
    )
    client, _models = stub_sdk(response)

    with pytest.raises(LlmError, match="cut off"):
        call_with_tools(client, [UserMessage(text="go")])


def test_a_blocked_request_is_not_retried(stub_sdk: Any) -> None:
    response = types.GenerateContentResponse(
        candidates=[],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        ),
    )
    client, _models = stub_sdk(response)

    with pytest.raises(LlmError, match="blocked") as caught:
        call_with_tools(client, [UserMessage(text="go")])
    assert caught.value.retryable is False
