"""The command-line entry point."""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from .ask import ask_command, chat_command
from .ingest import ingest_command

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Read a port tariff PDF and answer vessel queries about it.",
)


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show diagnostics")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


app.command("ingest")(ingest_command)
app.command("ask")(ask_command)
app.command("chat")(chat_command)


if __name__ == "__main__":  # pragma: no cover
    app()
