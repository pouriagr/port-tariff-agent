"""The `ask` and `chat` commands."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from ..agent.answer import TariffAnswer
from ..agent.factory import build_agent
from ..agent.loop import TariffAgent
from ..errors import AgentError, ConfigError, LlmError, PortTariffError
from ..settings import Settings, get_settings

EXIT_CONFIG = 1
EXIT_FAILED = 2

STOP_WORDS = {"exit", "quit", ":q"}


def _settings() -> Settings:
    try:
        return get_settings()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_CONFIG) from exc


def _amount(value: float | None) -> str:
    return f"{value:,.2f}" if value is not None else "-"


def _render(answer: TariffAnswer) -> None:
    typer.echo(answer.answer)
    if answer.charges:
        typer.echo("")
        for charge in answer.charges:
            typer.secho(
                f"{charge.name}  [{charge.section_id}, page {charge.page_citation}]", bold=True
            )
            typer.echo(f"  {charge.formula} = {_amount(charge.amount)}")
            for assumption in charge.assumptions:
                typer.echo(f"  - {assumption}")
        typer.echo("")
        typer.secho(f"Total {_amount(answer.total)} {answer.currency or ''}".strip(), bold=True)

    for missing in answer.missing_inputs:
        typer.secho(f"missing: {missing}", fg=typer.colors.YELLOW)
    for note in answer.notes:
        typer.echo(f"note: {note}")


def _answer(agent: TariffAgent, question: str) -> TariffAnswer:
    try:
        return agent.ask(question)
    except (AgentError, LlmError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_FAILED) from exc


def ask_command(
    query: Annotated[str, typer.Argument(help="The vessel call to price, in your own words")],
    as_json: Annotated[bool, typer.Option("--json", help="Print the answer as JSON")] = False,
    max_iterations: Annotated[
        int | None, typer.Option("--max-steps", min=1, max=60, help="Cap on model turns")
    ] = None,
) -> None:
    """Answer one question about a vessel call and exit."""
    settings = _settings()
    answer = _answer(build_agent(settings, max_iterations=max_iterations), query)
    if as_json:
        typer.echo(json.dumps(answer.model_dump(mode="json"), indent=2))
    else:
        _render(answer)


def chat_command(
    max_iterations: Annotated[
        int | None, typer.Option("--max-steps", min=1, max=60, help="Cap on model turns")
    ] = None,
) -> None:
    """Ask follow-up questions in one conversation. Type 'exit' to leave."""
    settings = _settings()
    agent = build_agent(settings, max_iterations=max_iterations)
    typer.echo("Describe the vessel call. Type 'exit' to leave.")
    while True:
        try:
            question = typer.prompt(">", prompt_suffix=" ").strip()
        except (EOFError, typer.Abort):
            return
        if not question:
            continue
        if question.lower() in STOP_WORDS:
            return
        try:
            _render(agent.ask(question))
        except PortTariffError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
