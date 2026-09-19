# Port Tariff Agent

An agentic RAG system that reads a port tariff PDF and, given a natural-language
description of a vessel call, finds, interprets and computes every port due the vessel
has to pay. Built for the Andersen Lab "Generative AI Solutions Developer" take-home test.

> Status: ingestion, query and validation are implemented and tested against the
> reference case below. The API, packaging and deployment steps are still open; see
> `docs/roadmap.md`.

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

The reference document is already ingested in this repository, so `ask` works straight
after `uv sync`:

```bash
uv run port-tariff ask "What port charges does the bulk carrier SUDESTADA pay at Durban?
Gross tonnage 51,300, LOA 229.2 m, arrived 2024-11-15, 3.39 days alongside,
exporting 40,000 t of iron ore."

uv run port-tariff ask "..." --json     # the structured answer
uv run port-tariff chat                 # follow-up questions in one conversation
```

Ingesting another tariff book, or re-ingesting this one:

```bash
uv run port-tariff ingest "data/raw/Port Tariff.pdf"          # skipped if already done
uv run port-tariff ingest "data/raw/Port Tariff.pdf" --force  # rebuild every step
```

Tests. The default run is offline and needs no API key, because the LLM conversations
behind the validation suite are recorded (`tests/cassettes/`):

```bash
uv run pytest                              # offline, this is what CI runs
uv run pytest -k record --live             # re-record the cassettes against the API
```

Still to come: `uvicorn port_tariff_agent.api:app` and a Dockerfile (roadmap phase 4).

## Accuracy report

Reference vessel: SUDESTADA (bulk carrier, GT 51,300) at the Port of Durban, TNPA
Tariff Book April 2024 to March 2025. Amounts in ZAR, excluding VAT.

The table below is generated from the recorded run and asserted against this file by
`tests/test_ground_truth.py`, so it cannot drift from what the agent actually computed.

<!-- accuracy-table:start -->
Recorded 2026-09-20 against `gemini-3.8-flash` (agent) and `gemini-3.8-flash` (charge selection), prompt `tariff_agent` v1 sha `90d1bdfabb59`, document `31471bc01ffe`. 10 model calls, 177,541 prompt and 3,608 output tokens.

| Charge | Section | Page | Expected | Computed | Deviation |
|---|---|---|---:|---:|---:|
| Light Dues | 1.1.1 | 9 | 60,062.04 | 60,062.04 | +0.000 % |
| VTS Dues | 2.1.1 | 11 | 33,315.75 | 33,345.00 | +0.088 % |
| Pilotage Dues | 3.3 | 13 | 47,189.94 | 47,189.94 | +0.000 % |
| Towage Dues | 3.6 | 15 | 147,074.38 | 147,074.38 | +0.000 % |
| Running Lines | 3.8 | 18 | 19,639.50 | 19,639.50 | +0.000 % |
| Port Dues | 4.1.1 | 21 | 199,549.22 | 199,371.35 | -0.089 % |
| **Total** | | | | **506,682.21** | |

Also priced, beyond the reference table: none.
<!-- accuracy-table:end -->

Four of the six reproduce to the cent. The two that do not are errors in the reference
figures rather than in the agent: the supplied Port Dues figure implies 3.396 days
alongside where the task states 3.39, and the supplied VTS figure is 0.65 times 51,255
rather than the stated GT of 51,300. `docs/spec/ground-truth.md` derives all six from the
document. The test's tolerance is 0.5 %, but a second assertion pins the other four to
0.05 % so a real regression cannot hide inside that slack.

"Running Lines" is section 3.8 *Berthing Services*, read from its "Other Ports" column
because Durban has no column of its own there. Section 3.9, literally titled "Running of
vessel lines", gives 3,309.12 and does not match the reference figure; see
`docs/spec/ground-truth.md`.

## Generality

The same document covers eight South African ports. The identical question is asked again
for Cape Town, with no code changed and nothing in the query naming a column.

<!-- generality:start -->
Recorded 2026-09-20 against `gemini-3.8-flash` (agent) and `gemini-3.8-flash` (charge selection), prompt `tariff_agent` v1 sha `90d1bdfabb59`, document `31471bc01ffe`. 15 model calls, 303,525 prompt and 3,861 output tokens.

| Charge | Section | Cape Town | Rate read from |
|---|---|---:|---|
| LIGHT DUES | 1.1.1 | 60,062.04 | the same rates |
| VTS CHARGES | 2.1.1 | 27,702.00 | this port's own column |
| PILOTAGE SERVICES | 3.3 | 23,149.98 | this port's own column |
| TUGS/VESSEL ASSISTANCE AND/OR ATTENDANCE | 3.6 | 109,186.98 | this port's own column |
| BERTHING SERVICES | 3.8 | 21,412.58 | this port's own column |
| PORT DUES | 4.1.1 | 199,371.35 | the same rates |
| **Total** | | **440,884.93** | |

Sections where Cape Town and Durban resolved to different constants: 2.1.1, 3.3, 3.6, 3.8.
<!-- generality:end -->

The same six charges apply, priced from different rows of the same tables. Light Dues and
Port Dues are nationwide and are identical at both ports. Section 3.8 is the interesting
one: Cape Town has a column of its own there, while Durban does not and falls back to
"Other Ports" — the opposite of section 3.3, where Durban has a column and the fallback is
unused. Nothing in the code or the prompt knows that.

Two tests enforce it, both in `tests/provenance.py` and neither containing a port name, a
charge name or a rate:

- **Provenance.** Every rate in a formula, minus the numbers the user stated, must appear
  verbatim in the text of the section the answer cites.
- **Column choice.** If the cited section's table has a column naming this port, the rate
  must come from it; if it has port columns but none for this port, the rate must come
  from a column naming no port. "A header that names a port" is decided against the
  registry's `ports` list, which ingestion derived from the document.

Ingesting a different authority's tariff book requires no code changes either; see
`docs/spec/ingestion.md`.

## Project layout

```
src/port_tariff_agent/   ingestion/, agent/, api/, cli/
tests/                   unit tests and the ground-truth integration test
data/                    raw PDFs and generated ingestion output
docs/                    roadmap.md, decisions.md, spec/
```

## Development workflow

This repository follows a spec-first workflow. Read `CLAUDE.md` before contributing.
