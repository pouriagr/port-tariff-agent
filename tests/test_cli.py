"""The command line: exit codes and what the user sees."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from port_tariff_agent.cli import app
from port_tariff_agent.cli import ingest as ingest_module
from port_tariff_agent.errors import LlmError
from port_tariff_agent.registry import load_registry
from port_tariff_agent.settings import Settings
from tests.fakes import FakeLlm
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
