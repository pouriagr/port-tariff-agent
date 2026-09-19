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

- [x] Settings (`pydantic-settings`): API key, model names, data dir; loads `.env.local`
- [x] Gemini client wrapper: structured output, retry with backoff on 429/5xx, bounded concurrency
- [x] Document hashing and folder layout under `data/<hash>/`
- [x] PageTranscriber: split PDF with pypdf, one call per page, per-page cache, join into `tariff.md`
- [x] Index builder: regex over numbered headings, flat list, `tariff_index.json`, helpers
      `get_node`, `get_with_children`, `get_context`, `page_citation`
- [x] ChargeClassifier: one call per section, skip only empty containers (ADR-016), `charges.json`
- [x] DocumentProfiler: front pages to metadata; append row to `documents.json`
- [x] CLI `port-tariff ingest <pdf> [--force]`
- [x] Unit tests: index builder (fixtures with synthetic Markdown), hash-skip, registry append,
      retry policy, concurrency, both LLM steps against a fake client, CLI, sanity checks
- [x] Run on `data/raw/Port Tariff.pdf`; eyeball `tariff.md` tables for the sections in
      `spec/ground-truth.md`; record transcription issues in `spec/pdf-notes.md`
      (27 pages, 98 sections, 66 charges; all six ground-truth sections reachable with
      their constants intact)
- [x] Decide the default models: newest 3.x Flash for extraction, newest Flash-Lite for
      classification (ADR-017)

## Phase 2: Query

Spec: `spec/query.md`

- [x] Document selection: port and arrival date against `documents.json`, newest `ingested_at` wins
      (landed in Phase 1 as `registry.select_document`; ADR-006 and ADR-018 are implemented there)
- [x] ChargeSelector: `charges.json` plus vessel description to applicable section ids with reasons.
      Also sees the sections that define no charge and returns `context_sections` (ADR-019), which
      resolves the general-terms gap recorded in `spec/pdf-notes.md`
- [x] Tool `get_charges`: selection, ChargeSelector, section texts with ancestors and pages
- [x] Tool `calculate`: AST-whitelisted evaluator (numbers, + - * / parentheses, ceil, floor,
      round, min, max). Joins a thousands space so a rate copied as printed evaluates
- [x] Tool `submit_answer`: validates `TariffAnswer` and ends the loop
- [x] TariffAgent: ReAct loop on `google-genai` function calling, system prompt, max iterations,
      full history kept per session. Neutral message and tool types keep the SDK in `llm/client.py`
      (ADR-020); tool arguments are rendered from their Pydantic models (ADR-021)
- [x] CLI `port-tariff ask "<query>"` and `port-tariff chat`
- [x] Unit tests: evaluator, schema rendering, selector output validation, `get_charges`, the loop
      and both commands, all offline against fakes (381 tests)

Live check against the reference query (2026-09-19, not a Phase 3 tick). All six ground-truth
sections were found and every amount landed inside the 0.5 percent tolerance: 1.1.1, 3.3, 3.6 and
3.8 exact, 4.1.1 at -0.089 percent and 2.1.1 at +0.088 percent, which are the two deviations
`spec/ground-truth.md` already attributes to the reference figures. What the first run got wrong was
fixed in the prompts, not in code:

- It charged the time-based fee on arrival-to-departure although the user had stated the days
  alongside. The prompt now says a quantity the user states outright beats one the model derives.
- It included a charge the document places on the cargo owner. The prompt now selects for the party
  the question is about, using the `payer` the catalog already records.

Two provider facts the live run surfaced, both handled in `llm/client.py`: a thinking model requires
the opaque signature on earlier tool-call parts to be sent back, and the SDK's automatic function
calling has to be disabled so the loop dispatches its own tools.

What Phase 3 needs: the reference run is reproducible with `port-tariff ask`; the recorded fixtures
have to capture a whole conversation (model turns with tool calls, signatures included), not single
responses, because the loop is what is being replayed.

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
