# Spec: Query phase

One ReAct agent, `TariffAgent`, answers the user's question using the ingested data. It
keeps the whole conversation so the user can ask follow-ups. Nothing in this phase
contains tariff knowledge; every rate, column choice and formula is read from the
section texts at run time.

Related ADRs: 006 to 011, 013, 019 to 021, 023.

The composition root is `agent/factory.py::build_agent(settings, *, max_iterations, client,
today)`, shared by the CLI and the API (ADR-026). `client` and `today` default to the provider and the system clock; both are
injectable because the answer depends on them — the validation suite replays a recorded
client, and document selection is a function of the arrival date against the day of the
query. `LlmClient` in `llm/protocol.py` is the two generators in one protocol, which is
what the agent is composed from.

## Flow

```
user message (free text or a JSON blob pasted by the user)
  -> TariffAgent loop (google-genai function calling, history = all prior turns,
     tool calls and tool results)
       1. reads the message, works out port, arrival date and a vessel description
       2. calls get_charges(...)                       once per port/date in question
       3. for each applicable section: reads the text, derives the formula for this port,
          calls calculate(expression) for every amount
       4. calls submit_answer(TariffAnswer)            ends the loop
  -> TariffAnswer returned to CLI / API; appended to history
```

Loop limits: at most 20 model turns per user message; on overrun the agent is asked
once to submit what it has, with a note in `notes`.

## Tools

### `get_charges(port: str, vessel_description: str, arrival_date: str | null)`

Knowledge tool. Behind it, in order:

1. **Document selection** (code). Load `documents.json`. Keep rows with `active == true`
   whose `ports` contain `port`, compared on the normalised port key of ADR-018. If
   `arrival_date` is given, keep rows whose validity contains it; otherwise use today's
   date. A `null` bound is unbounded on that side, so a row with unknown validity matches
   every date; a row whose stated range contains the date therefore wins over one that only
   matches because its validity is unknown. Among the rows that remain the newest
   `ingested_at` wins. If none remain, return
   `{"error": "No tariff document covers port X on date Y", "known_ports": [...]}`.
   Paths are derived from `DATA_DIR` and the row's `document_hash`; the row stores none.
2. **ChargeSelector** (LLM, one call, extraction model). Input: the `charges` array from
   the selected document's `charges.json` (section id, name, payer, applies_when), every
   other numbered section that has text of its own as id, title and a short preview
   (ADR-019), the port and the vessel description. Two questions: which of these charges
   apply to this vessel call, and which of the other sections state terms needed to
   interpret them. Response schema:

   ```json
   {
     "applicable": [{"section_id": "3.3", "reason": "Pilotage is compulsory at Durban"}],
     "context_sections": [{"section_id": "3.1", "reason": "Defines working hours and the
                           out-of-hours surcharge that the marine services refer to"}]
   }
   ```

   Code drops ids that do not exist in the index, de-duplicates, and removes from
   `context_sections` anything already in `applicable`.
3. **Section texts** (code). For each applicable and each context id, `get_context(id)`
   from `tariff_index.json` (ancestors' text, then the section with its children). Also
   the section's `page_citation`, which is its printed page number where the transcription
   captured one and a PDF page reference otherwise (ADR-015).

**Return value.**

```json
{
  "document": {"document_hash": "...", "issuer": "...", "title": "...", "currency": "ZAR",
               "valid_from": "2024-04-01", "valid_to": "2025-03-31"},
  "port": "Durban",
  "applicable": [
    {"section_id": "3.3", "name": "Pilotage Dues", "page_citation": "13",
     "reason": "Pilotage is compulsory at Durban", "text": "...full context text..."}
  ],
  "context": [
    {"section_id": "3.1", "name": "General", "page_citation": "12",
     "reason": "Defines working hours and the out-of-hours surcharge",
     "text": "...full context text..."}
  ],
  "not_applicable": [
    {"section_id": "4.2", "name": "Port dues for small vessels"}
  ]
}
```

`context` holds sections to read but not to charge for (ADR-019). `not_applicable` is the
complement of `applicable` over the charge catalog, computed by code, without reasons.

### `calculate(expression: str)`

Deterministic evaluator. Parses the expression with Python's `ast` and accepts only:
numeric literals, `+ - * /`, unary minus, parentheses, and calls to `ceil`, `floor`,
`round`, `min`, `max`. Anything else raises and the tool returns
`{"error": "..."}` so the agent can rewrite the expression.

Before parsing, a space between two digits is removed, so a rate copied out of the
document as printed (`73 118.07`) evaluates instead of failing on a syntax error. This is
the only normalisation; it never changes a value.

Return value: `{"expression": "...", "result": 47189.94}` with `result` rounded to
2 decimals (full-precision value also returned as `raw`).

### `submit_answer(answer: TariffAnswer)`

Control tool. Validates the payload against the `TariffAnswer` schema below and ends the
loop. Validation errors are returned to the agent, which fixes and resubmits.

## `TariffAnswer` schema

```json
{
  "answer": "For SUDESTADA at Durban, six charges apply, totalling ZAR 506,320.59 excluding VAT. ...",
  "port": "Durban",
  "vessel_summary": "Bulk carrier SUDESTADA, GT 51,300, LOA 229.2 m, 3.39 days alongside, exporting iron ore",
  "charges": [
    {
      "name": "Pilotage Dues",
      "section_id": "3.3",
      "page_citation": "13",
      "formula": "2 * (18608.61 + ceil(51300 / 100) * 9.72)",
      "amount": 47189.94,
      "assumptions": ["Two services: one entering, one leaving"]
    }
  ],
  "not_applicable": [
    {"section_id": "4.1.2", "name": "Berth Dues", "reason": "Vessel is handling cargo"}
  ],
  "missing_inputs": [],
  "total": 506320.59,
  "currency": "ZAR",
  "notes": ["Amounts exclude 15% VAT", "Durban operates 24 hours, no out-of-hours surcharge applied"]
}
```

- `charges[].amount` must equal a `calculate` result from this turn; the agent copies,
  never recomputes.
- `total` is also produced through `calculate`.
- `missing_inputs` names vessel data the agent needed and did not have; the related charge
  is omitted from `charges` and explained in `answer`.
- On follow-up turns that need no new calculation, `charges` may be empty and `total`
  `null`.

## System prompt outline for TariffAgent

- Role: a port tariff specialist who answers strictly from the tariff document sections
  returned by `get_charges`.
- Procedure: identify port, arrival date and vessel facts from the conversation; call
  `get_charges`; for each applicable section decide whether it truly applies, find the
  row and the column for this port (if the port has no column of its own, use the
  document's fallback column such as "Other Ports"); translate wording into a formula
  ("per 100 tons or part thereof" means round up to the next 100; "per service" means
  multiply by the number of services, normally one entering and one leaving unless the
  user says otherwise); apply surcharges, discounts or exemptions only when the
  conversation gives evidence for them and say so in `assumptions`.
- Every number goes through `calculate`. Never do arithmetic in prose.
- Never invent vessel data. Report gaps in `missing_inputs`.
- Cite `section_id` and `page_citation` for every charge.
- Finish with `submit_answer`.

The prompt contains no port names, charge names, rates or section numbers. Examples in
the prompt, if any, use placeholders.

## Chat history

- One session = one list of provider-neutral messages — `UserMessage`, `ModelTurn`
  (text plus tool calls) and `ToolResult` — which the client translates to the SDK's
  `Content` objects on each call (ADR-020). Each tool call carries an opaque signature
  the client round-trips, because a thinking model refuses a history that has lost it.
  The CLI `chat` command keeps one session in memory; the API keys sessions by
  `session_id` in an in-memory store (Phase 4).
- Follow-up messages ("why is towage that high?", "what if she stays 5 days?") reuse the
  history. The agent may call `calculate` again without calling `get_charges` if the
  section texts are already in the history.

## Error behaviour

- No document for the port or date: the tool returns an error object; the agent explains
  and lists the known ports.
- `calculate` rejects an expression: the agent rewrites it.
- Model turn limit reached: the agent submits a partial answer with a note.
- Rate limits: the client wrapper retries with backoff; the user sees a clear message if
  retries are exhausted.

## Inputs the agent should recognise

The user may paste structured vessel data like the task's example (`vessel_metadata`,
`technical_specs`, `operational_data`) or write prose. Either way the agent reads the
values it needs: vessel type, gross tonnage, length overall, arrival and departure
times, days alongside, cargo activity, number of operations. Field names are not fixed
in code.
