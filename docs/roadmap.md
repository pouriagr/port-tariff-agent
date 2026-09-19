# Roadmap

Status legend: `[ ]` not started, `[~]` in progress, `[x]` done. Tick items as they are
finished and add a short note when something was learned that later phases need.

## Phase 0: Scaffolding (2026-09-19)

- [x] Brainstorm and scope; decisions recorded in `decisions.md`
- [x] Gemini API key verified (models list, generateContent on 2.5 Flash, 3.x Flash, 2.5 Pro)
- [x] `uv` installed, `git init`, project skeleton, `pyproject.toml`
- [x] `CLAUDE.md`, `README.md` skeleton, `docs/roadmap.md`, `docs/decisions.md`
- [x] Specs: `spec/ingestion.md`, `spec/query.md`, `spec/ground-truth.md`, `spec/pdf-notes.md`
- [x] CI workflow (ruff check, ruff format check, pytest), smoke test, definition of done in `CLAUDE.md`
- [x] First commit and push to GitHub

## Phase 1: Ingestion

Spec: `spec/ingestion.md`

- [ ] Settings (`pydantic-settings`): API key, model names, data dir; loads `.env.local`
- [ ] Gemini client wrapper: structured output, retry with backoff on 429/5xx, bounded concurrency
- [ ] Document hashing and folder layout under `data/<hash>/`
- [ ] PageTranscriber: split PDF with pypdf, one call per page, per-page cache, join into `tariff.md`
- [ ] Index builder: regex over numbered headings, flat list, `tariff_index.json`, helpers
      `get_node`, `get_with_children`, `get_context`
- [ ] ChargeClassifier: one call per section, skip near-empty nodes, `charges.json`
- [ ] DocumentProfiler: front pages to metadata; append row to `documents.json`
- [ ] CLI `port-tariff ingest <pdf> [--force]`
- [ ] Unit tests: index builder (fixtures with synthetic Markdown), hash-skip, registry append
- [ ] Run on `data/raw/Port Tariff.pdf`; eyeball `tariff.md` tables for the sections in
      `spec/ground-truth.md`; record transcription issues in `spec/pdf-notes.md`
- [ ] Decide the default Flash model after comparing 2.5 Flash with the newest Flash on
      the two-column pages (ADR)

## Phase 2: Query

Spec: `spec/query.md`

- [ ] Document selection: port and arrival date against `documents.json`, newest `ingested_at` wins
- [ ] ChargeSelector: `charges.json` plus vessel description to applicable section ids with reasons
- [ ] Tool `get_charges`: selection, ChargeSelector, section texts with ancestors and pages
- [ ] Tool `calculate`: AST-whitelisted evaluator (numbers, + - * / parentheses, ceil, floor,
      round, min, max)
- [ ] Tool `submit_answer`: validates `TariffAnswer` and ends the loop
- [ ] TariffAgent: ReAct loop on `google-genai` function calling, system prompt, max iterations,
      full history kept per session
- [ ] CLI `port-tariff ask "<query>"` and `port-tariff chat`
- [ ] Unit tests: evaluator, document selection, selector output validation

## Phase 3: Validation

Spec: `spec/ground-truth.md`

- [ ] Integration test: SUDESTADA at Durban, six reference values, tolerance 0.5 percent,
      matched by section id
- [ ] Generality run: same vessel at Cape Town, no code changes; sanity-check column choice
- [ ] Recorded LLM responses (fixtures) so the test suite runs offline without a key
- [ ] Fill the accuracy report table in `README.md`

## Phase 4: API and packaging

- [ ] FastAPI: `POST /documents` (ingest), `POST /ask` (new or existing `session_id`),
      `GET /health`
- [ ] In-memory session store keyed by `session_id`
- [ ] Dockerfile (uv-based), `.dockerignore`
- [ ] Decide whether to ship the ingested cache for the reference PDF in the repo (ADR)

## Phase 5: Documentation

- [ ] README: setup, run, architecture, accuracy report, generality, limitations
- [ ] Prompt texts reviewed for anything tariff-specific (there must be none)
- [ ] Final pass on `decisions.md`

## Phase 6: Deployment (bonus)

- [ ] Pick a free host (Cloud Run, Render, or similar) once the code is done
- [ ] Deploy, add the live URL to the README
