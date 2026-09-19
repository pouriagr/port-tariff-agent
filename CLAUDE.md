# CLAUDE.md

Guidance for Claude Code and humans working in this repository.

## What this project is

An agentic RAG system that reads a port tariff PDF and answers natural-language vessel
queries by finding, interpreting and computing every port due the vessel has to pay.
It is the take-home test for Andersen Lab's "Generative AI Solutions Developer" role.

Evaluation criteria, in order of weight:

1. Accuracy against the ground truth (see `docs/spec/ground-truth.md`).
2. Level of automation: the agent must find and interpret the rules in the document.
   Nothing tariff-specific may be hard-coded.
3. Senior-level code quality: modular design, error handling, clean abstractions.

## Language

English only. Docs, code, comments, docstrings, commit messages and chat replies.
No other language anywhere in this repository.

## Spec-first workflow (spec memory pattern)

1. Before coding a phase, read its spec in `docs/spec/` and the related ADRs in
   `docs/decisions.md`. Do not start from memory of the conversation.
2. If the implementation needs a decision the spec does not cover, or a spec decision
   turns out to be wrong: write the ADR in `docs/decisions.md` first, update the spec,
   then write the code.
3. After finishing a step, tick it in `docs/roadmap.md` and record anything learned
   that the next step needs.
4. Code and spec never drift. The spec is the source of truth; the code follows it.

## Hard rules

- No tariff logic in Python. No charge names, rates, section numbers, port names or
  formulas in code or prompts. All of that is data discovered from the document
  (`charges.json`, `tariff_index.json`) or interpreted by the LLM at query time.
  Tests may reference section ids and expected values; production code may not.
- The LLM never does arithmetic. It writes a formula; the `calculate` tool evaluates it.
- The LLM never guesses missing vessel data. Missing inputs are reported, not invented.
- Secrets come only from the environment. `.env.local` (git-ignored) is loaded in
  development. `.env.example` documents every variable. Never commit a key.
- Deterministic where possible. Index building, document selection, caching and
  formula evaluation are plain code with unit tests. LLM calls are limited to the
  steps named in the specs.
- Every LLM call has a fixed Pydantic response schema and retries with backoff,
  because the Gemini free tier has low rate limits.
- Model names come from env (`GEMINI_MODEL_AGENT`, `GEMINI_MODEL_EXTRACT`), never
  from code.

## Definition of done for every development step

A step is not finished, and must not be reported as finished, until all of these hold:

1. New or updated tests in `tests/` cover the code that was written. Pure code gets unit
   tests. LLM-backed steps get tests against recorded responses so the suite runs
   offline. Ground-truth checks follow `docs/spec/ground-truth.md`.
2. `uv run pytest` passes.
3. `uv run ruff check .` and `uv run ruff format --check .` pass.
4. `docs/roadmap.md` is updated.

CI (`.github/workflows/ci.yml`) runs the same three commands on every push.

## Stack and commands

- Python 3.12, managed entirely with `uv`.
- `uv sync` installs; `uv run pytest` runs tests; `uv run ruff check .` lints;
  `uv run ruff format .` formats (`--check` to verify only).
- LLM access through the raw `google-genai` SDK. No LangChain, LlamaIndex or LangGraph.
- CLI with Typer, API with FastAPI, config with pydantic-settings.

## Layout

```
src/port_tariff_agent/
  ingestion/     PDF -> tariff.md -> tariff_index.json -> charges.json -> documents.json
  agent/         TariffAgent ReAct loop, tools get_charges / calculate / submit_answer
  api/           FastAPI app
  cli/           Typer app
tests/           unit tests plus the ground-truth integration test
data/            raw PDFs and generated ingestion output (see data/README.md)
docs/            roadmap, decisions, specs
task_docs/       original task material, git-ignored
```

## Where things are

- Roadmap and status: `docs/roadmap.md`
- Decisions (ADRs): `docs/decisions.md`
- Specs: `docs/spec/ingestion.md`, `docs/spec/query.md`, `docs/spec/ground-truth.md`,
  `docs/spec/pdf-notes.md`
- Data layout: `data/README.md`
