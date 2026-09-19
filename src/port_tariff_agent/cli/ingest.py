"""The `ingest` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from ..errors import ClassificationError, ConfigError, IngestionError, TranscriptionError
from ..ingestion.checks import Level
from ..ingestion.pipeline import IngestResult, ingest_document
from ..llm.client import GeminiClient
from ..settings import Settings, get_settings

EXIT_CONFIG = 1
EXIT_PARTIAL = 2


def build_client(settings: Settings) -> GeminiClient:
    """One place to swap the client, which is what the CLI tests patch."""
    return GeminiClient(settings)


def _report(result: IngestResult) -> None:
    typer.echo(
        f"pages {result.page_count} - sections {result.section_count} - "
        f"classified {result.classified} - skipped {result.skipped} - "
        f"charges {result.charge_count}"
    )
    for finding in result.findings:
        colour = typer.colors.RED if finding.level is Level.ERROR else typer.colors.YELLOW
        typer.secho(f"  {finding.level}: {finding.code}: {finding.message}", fg=colour)


def _echo_row(result: IngestResult, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(result.row.model_dump(by_alias=True, mode="json"), indent=2))
    else:
        row = result.row
        typer.echo(f"document {row.document_hash}")
        typer.echo(f"  title    {row.title or 'unknown'}")
        typer.echo(f"  issuer   {row.issuer or 'unknown'}")
        typer.echo(f"  currency {row.currency or 'unknown'}")
        typer.echo(f"  validity {row.valid_from or 'open'} to {row.valid_to or 'open'}")
        typer.echo(f"  ports    {', '.join(row.ports) or 'none found'}")


def _fail(message: str, items: list[str]) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    typer.secho(f"  {', '.join(items)}", fg=typer.colors.RED, err=True)
    typer.secho("  run the same command again to retry only what failed", err=True)
    raise typer.Exit(EXIT_PARTIAL)


def ingest_command(
    pdf: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Path to the tariff PDF"),
    ],
    force: Annotated[bool, typer.Option("--force", help="Ignore caches and ingest again")] = False,
    concurrency: Annotated[
        int | None, typer.Option("--concurrency", min=1, max=16, help="Parallel model calls")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the registry row as JSON")] = False,
) -> None:
    """Read a tariff PDF once and cache everything the agent needs to answer questions."""
    try:
        settings = get_settings()
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_CONFIG) from exc

    if concurrency is not None:
        settings = settings.model_copy(update={"ingest_concurrency": concurrency})

    with typer.progressbar(length=1, label="ingesting") as bar:
        state = {"step": "", "total": 1}

        def progress(step: str, done: int, total: int) -> None:
            if step != state["step"] or total != state["total"]:
                state.update(step=step, total=total)
                bar.length = max(total, 1)
                bar.label = step
                bar.pos = 0
            bar.update(done - bar.pos)

        try:
            result = ingest_document(
                pdf,
                settings=settings,
                client=build_client(settings),
                force=force,
                progress=progress,
            )
        except TranscriptionError as exc:
            _fail(
                "Some pages could not be transcribed (see the .error files beside them):",
                [str(page) for page in exc.failed_pages],
            )
        except ClassificationError as exc:
            _fail("Some sections could not be classified:", exc.section_ids)
        except IngestionError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(EXIT_PARTIAL) from exc

    if result.already_ingested:
        typer.echo("Already ingested; use --force to ingest again.")
        _echo_row(result, as_json)
        return

    _echo_row(result, as_json)
    _report(result)
