"""The command line: exit codes and what the user sees."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from port_tariff_agent.agent.loop import TariffAgent
from port_tariff_agent.agent.selector import ChargeSelector, Selection, SelectorResponse
from port_tariff_agent.cli import app
from port_tariff_agent.cli import ask as ask_module
from port_tariff_agent.cli import ingest as ingest_module
from port_tariff_agent.errors import ConfigError, LlmError
from port_tariff_agent.llm.protocol import ModelTurn, ToolCall, ToolResult, UserMessage
from port_tariff_agent.registry import load_registry
from port_tariff_agent.settings import Settings
from tests.conftest import Document
from tests.fakes import FakeLlm, FakeToolCallingLlm
from tests.test_agent import ANSWER
from tests.test_pipeline import responder

runner = CliRunner()


@pytest.fixture
def pdf(tiny_pdf: Callable[[int], Path]) -> Path:
    return tiny_pdf(3)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> FakeLlm:
    """Point the command at scripted responses and a temporary data directory."""
    llm = FakeLlm(default=responder)
    monkeypatch.setattr(ingest_module, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest_module, "build_client", lambda _settings: llm)
    return llm


def test_ingest_writes_every_artifact_and_reports(
    pdf: Path, wired: FakeLlm, settings: Settings
) -> None:
    result = runner.invoke(app, ["ingest", str(pdf)])

    assert result.exit_code == 0, result.output
    assert "Some Authority" in result.output
    assert "pages 3" in result.output
    assert "charges 2" in result.output
    assert len(load_registry(settings.data_dir)) == 1


def test_json_output_is_the_registry_row(pdf: Path, wired: FakeLlm) -> None:
    result = runner.invoke(app, ["ingest", str(pdf), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") : result.output.rindex("}") + 1])
    assert payload["document_hash"]
    assert payload["ports"] == ["Alpha Bay", "Bravo Point"]


def test_a_second_ingest_makes_no_calls(pdf: Path, wired: FakeLlm) -> None:
    runner.invoke(app, ["ingest", str(pdf)])
    calls_after_first = wired.call_count

    result = runner.invoke(app, ["ingest", str(pdf)])
    assert result.exit_code == 0, result.output
    assert "Already ingested" in result.output
    assert wired.call_count == calls_after_first


def test_force_ingests_again(pdf: Path, wired: FakeLlm, settings: Settings) -> None:
    runner.invoke(app, ["ingest", str(pdf)])
    calls_after_first = wired.call_count

    result = runner.invoke(app, ["ingest", str(pdf), "--force"])
    assert result.exit_code == 0, result.output
    assert wired.call_count > calls_after_first
    assert len(load_registry(settings.data_dir)) == 1


def test_a_missing_pdf_is_rejected_by_the_argument(wired: FakeLlm, tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", str(tmp_path / "nope.pdf")])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_a_failed_page_exits_with_the_partial_code(
    pdf: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def broken(request):
        if request.call_id == "transcribe/page_002":
            raise LlmError("page blew up", retryable=False)
        return responder(request)

    monkeypatch.setattr(ingest_module, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest_module, "build_client", lambda _settings: FakeLlm(default=broken))

    result = runner.invoke(app, ["ingest", str(pdf)])
    assert result.exit_code == 2
    assert "could not be transcribed" in result.output
    assert load_registry(settings.data_dir) == []


def test_missing_configuration_is_a_message_not_a_traceback(
    pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from port_tariff_agent.errors import ConfigError

    def unconfigured() -> Settings:
        raise ConfigError("Missing or invalid configuration: GEMINI_API_KEY")

    monkeypatch.setattr(ingest_module, "get_settings", unconfigured)

    result = runner.invoke(app, ["ingest", str(pdf)])
    assert result.exit_code == 1
    assert "GEMINI_API_KEY" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_concurrency_option_is_honoured(pdf: Path, wired: FakeLlm) -> None:
    result = runner.invoke(app, ["ingest", str(pdf), "--concurrency", "1"])
    assert result.exit_code == 0, result.output
    assert wired.max_in_flight == 1


def test_help_lists_the_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output


@pytest.fixture
def asking(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, document: Document
) -> FakeToolCallingLlm:
    """Point `ask` at a scripted conversation over the synthetic document."""
    client = FakeToolCallingLlm(
        [
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        name="get_charges",
                        args={
                            "port": document.port,
                            "vessel_description": "A vessel",
                            "arrival_date": "2024-06-01",
                        },
                    ),
                )
            ),
            ModelTurn(tool_calls=(ToolCall(name="submit_answer", args=ANSWER),)),
        ]
    )
    selector = ChargeSelector(
        FakeLlm(default=SelectorResponse(applicable=[Selection(section_id="1.2", reason="x")])),
        model="model-extract",
    )
    monkeypatch.setattr(ask_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        ask_module,
        "build_agent",
        lambda _settings, max_iterations=None: TariffAgent(
            client=client,
            model="model-agent",
            data_dir=document.data_dir,
            selector=selector,
        ),
    )
    return client


def test_ask_prints_the_answer_the_charges_and_the_total(asking: FakeToolCallingLlm) -> None:
    result = runner.invoke(app, ["ask", "What does my vessel pay?"])

    assert result.exit_code == 0, result.output
    assert "Two charges apply." in result.output
    assert "Arrival Fee" in result.output
    assert "1.2" in result.output
    assert "1,282.50" in result.output


def test_ask_can_print_the_answer_as_json(asking: FakeToolCallingLlm) -> None:
    result = runner.invoke(app, ["ask", "What does my vessel pay?", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["charges"][0]["formula"] == "ceil(51300 / 100) * 2.50"
    assert payload["total"] == 1282.5


def test_ask_passes_the_question_through_to_the_agent(asking: FakeToolCallingLlm) -> None:
    runner.invoke(app, ["ask", "How much for a call on the first of June?"])
    first = asking.histories[0][0]
    assert isinstance(first, UserMessage)
    assert first.text == "How much for a call on the first of June?"


def test_ask_reports_a_failure_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, document: Document
) -> None:
    def failing(_settings: Settings, max_iterations: int | None = None) -> TariffAgent:
        return TariffAgent(
            client=FakeToolCallingLlm([ModelTurn(text="no tools"), ModelTurn(text="still none")]),
            model="model-agent",
            data_dir=document.data_dir,
            selector=ChargeSelector(FakeLlm(default=SelectorResponse()), model="model-extract"),
            max_iterations=1,
        )

    monkeypatch.setattr(ask_module, "get_settings", lambda: settings)
    monkeypatch.setattr(ask_module, "build_agent", failing)

    result = runner.invoke(app, ["ask", "anything"])

    assert result.exit_code == 2
    assert "did not produce an answer" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_ask_without_configuration_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> Settings:
        raise ConfigError("GEMINI_API_KEY is not set")

    monkeypatch.setattr(ask_module, "get_settings", missing)

    result = runner.invoke(app, ["ask", "anything"])

    assert result.exit_code == 1
    assert "GEMINI_API_KEY" in result.output


def test_chat_answers_until_the_user_leaves(
    monkeypatch: pytest.MonkeyPatch, asking: FakeToolCallingLlm
) -> None:
    result = runner.invoke(app, ["chat"], input="What does my vessel pay?\nexit\n")

    assert result.exit_code == 0, result.output
    assert "Two charges apply." in result.output


class CombinedFake(FakeLlm, FakeToolCallingLlm):
    """One object answering both protocols, the way `GeminiClient` does.

    Both bases define `call_count`, so read `requests` (structured) and `calls`
    (tool-calling) directly rather than relying on which one the MRO picks.
    """

    def __init__(self, turns: list[ModelTurn], response: SelectorResponse) -> None:
        FakeLlm.__init__(self, default=response)
        FakeToolCallingLlm.__init__(self, turns)


class TestBuildAgent:
    """The composition root. A client passed in has to reach both model call sites."""

    def build(self, settings: Settings, document: Document, **kwargs: object) -> CombinedFake:
        client = CombinedFake(
            [
                ModelTurn(
                    tool_calls=(
                        ToolCall(
                            name="get_charges",
                            args={
                                "port": document.port,
                                "vessel_description": "A vessel",
                                "arrival_date": "2024-06-01",
                            },
                        ),
                    )
                ),
                ModelTurn(tool_calls=(ToolCall(name="submit_answer", args=ANSWER),)),
            ],
            SelectorResponse(applicable=[Selection(section_id="1.2", reason="x")]),
        )
        agent = ask_module.build_agent(
            settings.model_copy(update={"data_dir": document.data_dir}),
            client=client,
            **kwargs,  # type: ignore[arg-type]
        )
        agent.ask("What does my vessel pay?")
        return client

    def test_an_injected_client_serves_the_loop_and_the_selector(
        self, settings: Settings, document: Document
    ) -> None:
        client = self.build(settings, document)

        assert [call["call_id"] for call in client.calls] == ["agent/0", "agent/1"]
        assert [request.call_id for request in client.requests] == [f"select/{document.port}"]

    def test_each_call_site_gets_the_model_configured_for_it(
        self, settings: Settings, document: Document
    ) -> None:
        client = self.build(settings, document)

        assert client.calls[0]["model"] == settings.gemini_model_agent
        assert client.requests[0].model == settings.gemini_model_extract

    def test_the_injected_clock_is_what_document_selection_falls_back_to(
        self, settings: Settings, document: Document
    ) -> None:
        """With no arrival date the tool dates the call itself, and must use the given day.

        The synthetic document is valid through 2024 only, so a 1999 clock must find
        nothing rather than quietly matching on the real today.
        """
        client = CombinedFake(
            [
                ModelTurn(
                    tool_calls=(
                        ToolCall(
                            name="get_charges",
                            args={"port": document.port, "vessel_description": "A vessel"},
                        ),
                    )
                ),
                ModelTurn(tool_calls=(ToolCall(name="submit_answer", args=ANSWER),)),
            ],
            SelectorResponse(applicable=[Selection(section_id="1.2", reason="x")]),
        )
        agent = ask_module.build_agent(
            settings.model_copy(update={"data_dir": document.data_dir}),
            client=client,
            today=date(1999, 12, 31),
        )

        agent.ask("What does my vessel pay?")

        payload = next(
            message.payload for message in agent.history if isinstance(message, ToolResult)
        )
        assert "1999-12-31" in payload["error"]

    def test_without_a_client_it_still_builds_the_provider_one(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> None:
        built: list[Settings] = []
        monkeypatch.setattr(
            ask_module, "GeminiClient", lambda config: built.append(config) or object()
        )

        ask_module.build_agent(settings)

        assert built == [settings]
