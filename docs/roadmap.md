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

- [x] Ship the reference document's query-time artifacts so the suite has a document in CI
      (ADR-022). This also resolves the Phase 4 question below
- [x] Recorded LLM responses: one ordered cassette per run covering both model call sites,
      signatures included (`tests/cassettes.py`, ADR-023). Replay recomputes tool results
      from the committed artifacts, so the loop, the toolbox and the evaluator are all
      exercised; only the model is stubbed
- [x] Integration test: SUDESTADA at Durban, six reference values, tolerance 0.5 percent,
      matched by section id. Four reproduce to the cent; a second assertion pins those four
      to 0.05 percent so a regression cannot hide in the tolerance
- [x] Generality run: same vessel at Cape Town, no code changes. Asserted as rate provenance
      and column choice rather than expected values (`tests/provenance.py`, ADR-024)
- [x] Fill the accuracy report table in `README.md`, rendered from the recorded run and
      asserted against the file (`tests/report.py`, ADR-025)

Result. All six reference values land inside tolerance: 1.1.1, 3.3, 3.6 and 3.8 exact, 2.1.1
at +0.088 percent and 4.1.1 at -0.089 percent, the two deviations `spec/ground-truth.md`
attributes to the reference figures themselves. Durban total 506,682.21 ZAR over 10 model
calls. Cape Town prices the same six sections for 440,884.93 ZAR, with 2.1.1, 3.3, 3.6 and
3.8 resolved to its own column; 3.8 is the case that matters, because Durban has no column
there and falls back to "Other Ports" while Cape Town has one.

What was found along the way:

- `_read_turn` collected the turn-level thought signature and never put it on the `ModelTurn`
  it returned, so it was silently dropped. Fixed, with `tests/test_llm_translation.py`
  covering both directions of the SDK translation.
- The `.gitignore` rule had to become `data/*/*`: git does not descend into an excluded
  directory, so a negation under `data/*/` never fires.

What Phase 4 needs: `build_agent(settings, *, max_iterations, client, today)` is the
composition root, and the `client` and `today` arguments are the seam `POST /ask` and its
session store should use. `LlmClient` in `llm/protocol.py` is both generators in one
protocol.

## Phase 4: API and packaging

Spec: `spec/api.md`

- [x] FastAPI: `POST /documents` (ingest), `POST /ask` (new or existing `session_id`),
      `GET /health`, plus `GET /documents/jobs/{job_id}` and `GET /documents`.
      `create_app()` owns the stores; `app` at module level is what uvicorn serves
- [x] In-memory session store keyed by `session_id`: one lock per session, bounded by count
      and idle time, unknown id is 404 and a concurrent turn is 409 (ADR-028)
- [x] Ingestion answers 202 with a job id and runs as a background task, because a tariff
      book is minutes of model calls (ADR-027). One lock serialises writes to the registry
- [x] One table maps every `PortTariffError` to a status and a stable code, applied by a
      single handler, so routes hold no try/except (ADR-029)
- [x] Dockerfile (uv-based), `.dockerignore`. Multi-stage, non-root, the committed
      query-time artifacts baked in, one worker on `$PORT` (ADR-031)
- [x] Unit and integration tests: the stores with an injected clock, and every endpoint
      against the existing fakes, offline (52 new tests, 566 in total)
- [x] Decide whether to ship the ingested cache for the reference PDF in the repo (ADR).
      Resolved early in Phase 3: the query-time artifacts are committed, the transcription
      cache is not (ADR-022)

Two things had to change outside `api/` before any of it worked:

- `build_agent` moved from `cli/ask.py` to `agent/factory.py` (ADR-026). The API importing
  the CLI would have inverted the layering; both entry points now compose through the agent
  layer, and so does the validation suite.
- `tests/conftest.py::no_network` blocked every `socket.connect`, which made `TestClient`
  unusable on Windows: an asyncio loop there builds its self-pipe from a loopback socket
  pair. Linux CI would have stayed green while the whole surface went untested locally.
  The guard now allows loopback and still blocks name resolution outright (ADR-030).

Also found: `python-multipart` was missing. FastAPI raises when a route with an upload is
*registered*, not when it is called, so its absence would have broken `import
port_tariff_agent.api` and the smoke test with it.

Verified against the real image: `docker build`, then `/health` reporting the one ingested
document and `port-tariff --help` running inside the same container. The image is 1.4 MB of
data on a slim base; `.dockerignore` keeps the transcription cache out.

Live check over HTTP (2026-09-20). `POST /ask` with the reference query returned the same
six sections and the same total as the Phase 3 recorded run, 506,682.21 ZAR, in 59 seconds.
A follow-up on the returned `session_id` answered from the history in 11 seconds without
calling `get_charges` again, which is the point of keeping the agent alive per session.
One transcription-level `ReadTimeout` was retried by the client wrapper and never reached
the caller.

What Phase 5 needs: the Limitations section must state that sessions are in memory, that
the service runs one worker, and that there is no authentication. `docs/spec/api.md` has
the wording under "Limits and non-goals".

## Phase 5: Documentation

- [x] README: setup, run, architecture, accuracy report, generality, limitations.
      `## Limitations` sits after Generality and covers the service, the accuracy claim and
      the transcription; the API-scoped `### Limits` is gone rather than duplicated
- [x] Prompt texts reviewed for anything tariff-specific (there must be none). None found;
      the line the review applied is ADR-032
- [x] Re-record both cassettes and refresh the README blocks, after the prompt review.
      Not run, deliberately: see the note below. `uv run pytest -k record --live` stays
      documented in the README as what to run when a prompt does change
- [x] Final pass on `decisions.md`: ADR-032 and ADR-033 appended, history left as written

The prompt review found nothing tariff-specific: a grep over `src/` for port names, charge
names, rates, section numbers and the vessel's name returns zero hits, and no prompt was
edited. What it did find are five rules in `tariff_agent.md` that are abstract in wording but
were written after watching this document be read wrongly — the fallback column, the banded
increment, "per N units or part thereof", a service taken both ways, and a stated quantity
beating a derived one. ADR-032 keeps all five and states the line: a prompt may say how to
read a tariff document, never what this one says. Each rule would help on another
authority's book, and none can produce a figure without a rate the tool returned.

The re-record was skipped because nothing it exists for had happened. ADR-023 makes a
prompt edit warn rather than fail, and the re-record is how the claim is made true again —
but no prompt changed, both tapes still stamp `tariff_agent v1 sha 90d1bdfabb59` and
`charge_selection v1 sha 4fc907485c4b`, which match the files on disk, so no `CassetteStale`
warning fires and both recordings are dated 2026-09-20. Spending the free-tier calls would
have reproduced a claim that was already current, and put the hand-written prose around both
tables and `KNOWN_DEVIATIONS` at risk for nothing. Re-record when a stamped prompt changes.

Found and fixed along the way: the README block comparison kept only lines starting with
`|`, so the provenance caption — recording date, both models, prompt version and sha,
document hash, model-call count, token totals — was published and asserted by nothing, and
every re-record would have left it silently stale. `normalise` now compares each block
whole, prose included, wrap-insensitively (ADR-033); the current README passed unchanged,
which is how we know the gap had not yet bitten. Also: `.env.example` documented five of
eleven settings, which breaks a hard rule, so the six missing ones are in and
`tests/test_settings_env.py` keeps the file complete in both directions.

What Phase 6 needs: the live URL goes into the status blockquote at the top of the README.
If the host runs more than one instance, the first paragraph of `## Limitations` stops being
true — sessions would scatter across instances and the registry's single writer lock would
no longer hold.

## Phase 6: Deployment (bonus)

Spec: `spec/deployment.md`

- [x] Pick a free host (Cloud Run, Render, or similar) once the code is done. Render's free
      plan: no card, Docker built from the repository, a Blueprint in the repo (ADR-034).
      Zeabur is the fallback if signup asks for a card. Cloud Run needs a billing account,
      Koyeb and Fly.io a card, and Hugging Face Docker Spaces are paid now
- [x] Pipeline: `render.yaml` with `autoDeploy: false`; a CI `deploy` job after `check` on
      `main` fires the deploy hook pinned to the pushed sha and waits until `/health` reports
      that sha as `revision` (ADR-035). `GET /` redirects to `/docs`. The job is skipped until
      the `LIVE_URL` variable exists, so it can land before the service does.
      `tests/test_deployment_config.py` asserts the blueprint and the job against the spec
- [ ] Deploy, add the live URL to the README
