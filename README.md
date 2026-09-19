# Port Tariff Agent

An agentic RAG system that reads a port tariff PDF and, given a natural-language
description of a vessel call, finds, interprets and computes every port due the vessel
has to pay. Built for the Andersen Lab "Generative AI Solutions Developer" take-home test.

> Status: scaffolding. The architecture and specs are complete (`docs/`); the
> implementation follows the roadmap in `docs/roadmap.md`.

## Overview

Port tariff books are dense, hierarchical and full of conditional tables. Instead of
hard-coding any of that, this system:

1. **Ingests** a tariff PDF once into a machine-readable form: a clean Markdown
   transcription, an index of numbered sections, and a catalog of the payable charges
   the document defines.
2. **Answers** vessel queries with a ReAct agent that reads only the relevant sections,
   works out the formula for the requested port, and delegates every calculation to a
   deterministic tool.

Nothing in the code knows about Durban, pilotage or gross tonnage. Point it at another
port's tariff book and it builds a new catalog.

## Architecture

### Ingestion (once per document)

```
PDF --PageTranscriber (LLM, per page)--------> tariff.md
    --index builder (code)-------------------> tariff_index.json
    --ChargeClassifier (LLM, per section)----> charges.json
    --DocumentProfiler (LLM, front pages)----> documents.json (registry row)
```

### Query (per conversation)

```
user query --> TariffAgent (ReAct loop, chat history)
                 |- get_charges(port, vessel_description, arrival_date)
                 |     `- selects document -> ChargeSelector (LLM) -> section texts
                 |- calculate(expression)          deterministic evaluator
                 `- submit_answer(TariffAnswer)    validated JSON + free-text answer
```

Details: `docs/spec/ingestion.md`, `docs/spec/query.md`. Design rationale:
`docs/decisions.md`.

## Setup

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), a Gemini API key from
[Google AI Studio](https://aistudio.google.com/apikey).

```bash
uv sync
cp .env.example .env.local   # then fill in GEMINI_API_KEY
```

## Run

TBD in development. Planned surface:

```bash
uv run port-tariff ingest "data/raw/Port Tariff.pdf"
uv run port-tariff ask "Calculate all port charges for bulk carrier SUDESTADA, GT 51300, LOA 229.2 m, calling at Durban on 15 Nov 2024 ..."
uv run uvicorn port_tariff_agent.api:app --reload
docker build -t port-tariff-agent . && docker run --env-file .env.local -p 8000:8000 port-tariff-agent
```

## Accuracy report

Reference vessel: SUDESTADA (bulk carrier, GT 51,300) at the Port of Durban, TNPA
Tariff Book April 2024 to March 2025. Amounts in ZAR, excluding VAT.

| Charge | Expected | Computed | Deviation | Notes |
|---|---:|---:|---:|---|
| Light Dues | 60,062.04 | TBD | | |
| Port Dues | 199,549.22 | TBD | | |
| Towage Dues | 147,074.38 | TBD | | |
| VTS Dues | 33,315.75 | TBD | | |
| Pilotage Dues | 47,189.94 | TBD | | |
| Running Lines | 19,639.50 | TBD | | see `docs/spec/ground-truth.md` on section 3.8 vs 3.9 |

## Generality

The same document covers eight South African ports. The agent is exercised on a second
port (Cape Town) with no code changes to demonstrate that port-specific columns and
fallback columns are resolved from the document, not from code. Ingesting a different
authority's tariff book requires no code changes either; see `docs/spec/ingestion.md`.

## Project layout

```
src/port_tariff_agent/   ingestion/, agent/, api/, cli/
tests/                   unit tests and the ground-truth integration test
data/                    raw PDFs and generated ingestion output
docs/                    roadmap.md, decisions.md, spec/
```

## Development workflow

This repository follows a spec-first workflow. Read `CLAUDE.md` before contributing.
