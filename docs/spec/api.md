# Spec: API

An HTTP surface over the same composition roots as the CLI, with no logic of its own.
Ingestion is asynchronous because it costs minutes of model calls; querying is synchronous
because it costs seconds. Nothing here is tariff-specific: the API moves a question and an
answer, and never looks inside either.

Related ADRs: 026 to 031. Sessions were specified in `spec/query.md` and are realised here.

## Surface

| Method | Path | Success | Errors |
| --- | --- | --- | --- |
| `GET` | `/health` | 200 | none |
| `POST` | `/ask` | 200 | 404, 409, 422, 502, 503 |
| `POST` | `/documents` | 202 | 413, 415, 422, 503 |
| `GET` | `/documents/jobs/{job_id}` | 200 | 404 |
| `GET` | `/documents` | 200 | 503 |

OpenAPI is served at `/docs` and `/openapi.json` by FastAPI.

## Composition

`create_app()` builds the application and its two stores; `app = create_app()` at module
level is what `uvicorn port_tariff_agent.api:app` serves. Nothing in `create_app` reads
settings, opens a file or constructs a model client, because `tests/test_smoke.py` imports
the package with no key in the environment. Configuration is resolved per request, so a
missing key is a 503 on the first call rather than a crash at import.

Two seams, one per surface, mirroring the CLI's two composition roots (ADR-026):

| Surface | Dependency | Returns | CLI counterpart |
| --- | --- | --- | --- |
| `POST /ask` | `provide_agent_factory` | `Callable[[], TariffAgent]` | `build_agent` |
| `POST /documents` | `provide_client` | `LlmClient` | `cli/ingest.py::build_client` |

Tests replace them with `app.dependency_overrides`, not by patching module attributes:
`Depends(f)` captures the function object when the route is registered, so a later
`setattr` on the module has no effect. Because each test calls `create_app()`, the stores
and the overrides are per-test and there is nothing to reset.

A factory rather than a ready agent: an agent is built only when a session is created, and
a test can inject one with a low iteration cap to exercise the give-up path.

## POST /ask

```json
{"question": "free text or a pasted vessel JSON", "session_id": "optional"}
```

Omit `session_id` to start a conversation; pass the one that came back to continue it. The
response is the session id and a `TariffAnswer` exactly as `spec/query.md` defines it:

```json
{"session_id": "kZ1p...", "answer": {"answer": "...", "charges": [], "total": null}}
```

The endpoint is a plain `def`, so Starlette runs the blocking `ask` in its worker
threadpool. There is no per-request cap on model turns: `TariffAgent` fixes
`max_iterations` at construction, and one session is one agent.

Lifecycle:

- no `session_id` — mint one, build an agent, answer.
- known `session_id` — reuse that agent, so the history and the section texts already in
  it are reused.
- unknown or expired `session_id` — 404 `session_not_found`. The caller is told to omit the
  field to start a new conversation. Minting a fresh session silently would hand back an
  amnesiac agent with no signal that the history was lost.
- a turn already running on that session — 409 `session_busy`.

## Sessions

In memory only, for the reason `spec/query.md` gives: a turn carries opaque provider
signatures that must round-trip unchanged, so a conversation is a live `TariffAgent`, not a
serialisable record (ADR-028).

- Id: `secrets.token_urlsafe`. Knowing the id grants access to that conversation, so it is
  a capability, not a counter.
- One lock per session, taken for the duration of a turn. `ask` appends to `self.history`;
  two turns at once would interleave those appends.
- Bounded by entry count and by idle time, evicted least-recently-used first. A session
  whose lock is held is never evicted.
- The bounds are constructor arguments with module defaults, not `Settings` fields.

## POST /documents

`multipart/form-data` with one file field, `file`. The response is 202 with the job id and
a `Location` header pointing at the job:

```json
{"job_id": "Qm4...", "status": "running"}
```

The upload is copied to a private temporary directory inside the request, because an
`UploadFile` is closed when the request ends and the work outlives it. The filename is
reduced to its basename before use: it is attacker-controlled, and `ingest_document`
records it as the registry row's `source`. Rejections: a name that is not a PDF, or an
empty body, is 415; more than the size limit is 413.

A document that is already ingested is not special-cased into a synchronous 200. The job
simply reaches `done` almost at once with `already_ingested` set. One contract, one code
path, and answering the question at all means hashing the file and running the staleness
check that `ingest_document` owns.

## Jobs

A job carries its status, the current step, the progress counters, and on success the
registry row, the counts and the findings that `port-tariff ingest` prints:

```json
{"job_id": "Qm4...", "status": "done", "step": "classify",
 "progress": {"step": "classify", "done": 98, "total": 98},
 "document": {"document_hash": "...", "ports": ["..."]},
 "already_ingested": false,
 "counts": {"pages": 27, "sections": 98, "classified": 98, "skipped": 0, "charges": 66},
 "findings": [], "error": null}
```

`status` is `running`, `done` or `failed`. `error` is the same envelope as an HTTP error, so
a failed transcription still reports its pages in `details`. The job's `paths` are never
serialised: they are absolute paths on the server.

Progress is the existing `ProgressFn` passed straight into `ingest_document`, written under
a lock so a poll never reads a torn triple. Ingests are serialised by one process-wide lock,
because `registry.upsert_row` read-modify-writes `documents.json` with no file lock; a job
waiting on it stays `running`. The task catches every exception and records it on the job:
a background task that raises leaves the job stuck on `running` forever.

Jobs are bounded and expire on idle time like sessions. An unknown id is 404 `job_not_found`.

## GET /documents

The registry as a list of rows, serialised by alias so the key is `document_hash`. This is
what tells a caller which ports and which validity periods the service can answer for.

## GET /health

Checks only what can be checked with no key and no network call, and never calls a model:

```json
{"status": "ok", "configured": true, "documents": 1, "sessions": 0, "jobs": 0}
```

`status` is `degraded` when configuration is missing or the data directory cannot be read,
and `documents` is then null. It returns 200 in both cases: a probe that fails with 503
hides the message that says what is wrong.

## Errors

One envelope everywhere, including inside a job:

```json
{"error": {"code": "session_not_found", "message": "...", "details": null}}
```

| Exception | Status | `code` | `details` |
| --- | --- | --- | --- |
| `ConfigError` | 503 | `not_configured` | |
| `LlmError(retryable=True)` | 503 | `llm_unavailable` | plus a `Retry-After` header |
| `LlmError` | 502 | `llm_failed` | |
| `AgentError` | 502 | `agent_gave_up` | |
| `TranscriptionError` | 500 | `transcription_failed` | `failed_pages` |
| `ClassificationError` | 500 | `classification_failed` | `section_ids` |
| `IngestionError` | 500 | `ingestion_failed` | |
| `DocumentNotFoundError` | 404 | `document_not_found` | |
| other `PortTariffError` | 500 | `internal` | |
| `SessionNotFoundError` | 404 | `session_not_found` | |
| `SessionBusyError` | 409 | `session_busy` | `session_id` |
| `UnsupportedUploadError` | 415 | `unsupported_media_type` | `filename` |
| `UploadTooLargeError` | 413 | `payload_too_large` | `limit_bytes` |
| `RequestValidationError` | 422 | `invalid_request` | the validation errors |

One handler is registered on `PortTariffError` and one on the API-local `ApiError`;
Starlette looks a handler up along the exception's method resolution order, so a subclass
needs no registration of its own. Routes contain no try/except. An exception that is
neither is left to Starlette on purpose: it is a bug, and a test should see the traceback.

The four API-local errors do not subclass `PortTariffError`. "This session is busy" is an
HTTP concurrency concern and has no place in the domain hierarchy the CLI also uses.

## Limits and non-goals

- One process. Sessions are in memory and the registry has one in-process writer lock, so
  the service runs with a single worker (ADR-031).
- No authentication. Anyone who can reach the port can spend model calls.
- No cancel, no resumable upload, no streaming answer, and no server-sent progress: a job
  is polled.
- Concurrency on `/ask` is bounded by the framework's threadpool, and each turn holds one
  thread for its duration.
