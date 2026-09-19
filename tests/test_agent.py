"""The loop: tools are dispatched, failures come back as turns, and the answer is validated."""

from __future__ import annotations

import pytest

from port_tariff_agent.agent.loop import TariffAgent
from port_tariff_agent.agent.selector import ChargeSelector, Selection, SelectorResponse
from port_tariff_agent.agent.tools import CALCULATE, GET_CHARGES, SUBMIT_ANSWER
from port_tariff_agent.errors import AgentError
from port_tariff_agent.llm.protocol import ModelTurn, ToolCall, ToolResult, UserMessage
from tests.conftest import Document
from tests.fakes import FakeLlm, FakeToolCallingLlm

ANSWER = {
    "answer": "Two charges apply.",
    "port": "Northaven",
    "charges": [
        {
            "name": "Arrival Fee",
            "section_id": "1.2",
            "page_citation": "4",
            "formula": "ceil(51300 / 100) * 2.50",
            "amount": 1282.5,
            "assumptions": ["Rounded up to the next 100 tons"],
        }
    ],
    "total": 1282.5,
    "currency": "XTS",
}


def call(name: str, **args: object) -> ModelTurn:
    return ModelTurn(tool_calls=(ToolCall(name=name, args=args),))


def build(
    document: Document, turns: list, **kwargs: object
) -> tuple[TariffAgent, FakeToolCallingLlm]:
    client = FakeToolCallingLlm(turns)
    selector = ChargeSelector(
        FakeLlm(default=SelectorResponse(applicable=[Selection(section_id="1.2", reason="x")])),
        model="model-extract",
    )
    agent = TariffAgent(
        client=client,
        model="model-agent",
        data_dir=document.data_dir,
        selector=selector,
        **kwargs,  # type: ignore[arg-type]
    )
    return agent, client


def test_a_full_turn_fetches_computes_and_submits(document: Document) -> None:
    agent, client = build(
        document,
        [
            call(
                GET_CHARGES,
                port="Northaven",
                vessel_description="A vessel",
                arrival_date="2024-06-01",
            ),
            call(CALCULATE, expression="ceil(51300 / 100) * 2.50"),
            call(SUBMIT_ANSWER, **ANSWER),
        ],
    )
    answer = agent.ask("What does my vessel pay?")
    assert answer.total == 1282.5
    assert answer.charges[0].section_id == "1.2"
    assert client.call_count == 3


def test_the_tool_result_is_put_back_into_the_history(document: Document) -> None:
    agent, client = build(
        document,
        [call(CALCULATE, expression="2 + 2"), call(SUBMIT_ANSWER, **ANSWER)],
    )
    agent.ask("anything")
    results = [item for item in agent.history if isinstance(item, ToolResult)]
    assert results[0].payload == {"expression": "2 + 2", "result": 4.0, "raw": 4.0}


def test_a_rejected_expression_comes_back_as_an_error_the_model_can_fix(document: Document) -> None:
    agent, _ = build(
        document,
        [
            call(CALCULATE, expression="gt * 2"),
            call(CALCULATE, expression="51300 * 2"),
            call(SUBMIT_ANSWER, **ANSWER),
        ],
    )
    agent.ask("anything")
    payloads = [item.payload for item in agent.history if isinstance(item, ToolResult)]
    assert "error" in payloads[0]
    assert payloads[1]["result"] == 102600.0


def test_an_invalid_answer_is_rejected_and_the_loop_continues(document: Document) -> None:
    agent, _ = build(
        document,
        [call(SUBMIT_ANSWER, port="Northaven"), call(SUBMIT_ANSWER, **ANSWER)],
    )
    answer = agent.ask("anything")
    payloads = [item.payload for item in agent.history if isinstance(item, ToolResult)]
    assert "error" in payloads[0]
    assert payloads[0]["details"] == ["answer: Field required"]
    assert answer.answer == "Two charges apply."


def test_an_unknown_tool_is_reported_rather_than_raised(document: Document) -> None:
    agent, _ = build(document, [call("look_it_up"), call(SUBMIT_ANSWER, **ANSWER)])
    agent.ask("anything")
    payloads = [item.payload for item in agent.history if isinstance(item, ToolResult)]
    assert "no tool called" in payloads[0]["error"]


def test_prose_without_a_tool_call_is_nudged(document: Document) -> None:
    agent, _ = build(
        document,
        [ModelTurn(text="The total is about 1200."), call(SUBMIT_ANSWER, **ANSWER)],
    )
    agent.ask("anything")
    nudges = [item for item in agent.history if isinstance(item, UserMessage)]
    assert SUBMIT_ANSWER in nudges[-1].text


def test_the_step_limit_ends_with_one_last_chance_to_submit(document: Document) -> None:
    agent, client = build(
        document,
        [
            ModelTurn(text="thinking"),
            ModelTurn(text="still thinking"),
            call(SUBMIT_ANSWER, **ANSWER),
        ],
        max_iterations=2,
    )
    answer = agent.ask("anything")
    assert answer.total == 1282.5
    assert client.calls[-1]["tool_names"] == [SUBMIT_ANSWER]


def test_an_agent_that_never_submits_fails_loudly(document: Document) -> None:
    agent, _ = build(document, [ModelTurn(text="a"), ModelTurn(text="b")], max_iterations=1)
    with pytest.raises(AgentError, match="did not produce an answer"):
        agent.ask("anything")


def test_a_follow_up_keeps_the_earlier_conversation(document: Document) -> None:
    agent, client = build(
        document,
        [call(SUBMIT_ANSWER, **ANSWER), call(SUBMIT_ANSWER, **ANSWER)],
    )
    agent.ask("first question")
    agent.ask("second question")
    questions = [item.text for item in client.last_history() if isinstance(item, UserMessage)]
    assert questions == ["first question", "second question"]


def test_every_call_carries_the_system_prompt_and_all_three_tools(document: Document) -> None:
    agent, client = build(document, [call(SUBMIT_ANSWER, **ANSWER)])
    agent.ask("anything")
    assert client.calls[0]["tool_names"] == [GET_CHARGES, CALCULATE, SUBMIT_ANSWER]
    assert "tariff specialist" in str(client.calls[0]["system_instruction"])
    assert client.calls[0]["model"] == "model-agent"


def test_get_charges_reaches_the_document_on_disk(document: Document) -> None:
    agent, _ = build(
        document,
        [
            call(
                GET_CHARGES,
                port="Northaven",
                vessel_description="A vessel",
                arrival_date="2024-06-01",
            ),
            call(SUBMIT_ANSWER, **ANSWER),
        ],
    )
    agent.ask("anything")
    payload = next(item.payload for item in agent.history if isinstance(item, ToolResult))
    assert payload["applicable"][0]["name"] == "Arrival Fee"


def test_a_malformed_date_is_explained_rather_than_guessed(document: Document) -> None:
    agent, _ = build(
        document,
        [
            call(GET_CHARGES, port="Northaven", vessel_description="v", arrival_date="15 Nov 2024"),
            call(SUBMIT_ANSWER, **ANSWER),
        ],
    )
    agent.ask("anything")
    payload = next(item.payload for item in agent.history if isinstance(item, ToolResult))
    assert "YYYY-MM-DD" in payload["error"]
