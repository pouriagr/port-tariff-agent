# Spec: Ingestion

Turn one tariff PDF into a cached, machine-readable document that the query phase can
use without touching the PDF again. Runs once per document. Nothing here knows anything
about a specific port or charge.

Related ADRs: 001 to 006, 011, 015 to 018.

## Trigger and idempotency

- Entry point: `port-tariff ingest <pdf> [--force]` (and `POST /documents` later).
- Compute `document_hash = sha256(pdf bytes)`.
- If `documents.json` already has a row with that hash and `--force` is not set: print the
  existing row and stop. The registry row is written only by the last step, so its presence
  means all four steps finished.
- With `--force`: ignore every cache for this hash, re-run all four steps and replace the row
  that carries the same hash. "Rows are never overwritten" governs editions, and a different
  edition is a different hash, so re-ingesting identical bytes must not append a duplicate.
- Output folder: `DATA_DIR/<document_hash>/`.
- Every step writes its output to disk before the next step starts, so a crash resumes
  from the last completed step. Step outputs carry a `prompt_version` and the sha of the
  prompt text; either one changing invalidates that step's cache, so a forgotten version
  bump cannot silently reuse output produced by different instructions.
- `document_hash` is the field name in every persisted file, registry rows included. Readers
  also accept the older name `hash`; writers only ever emit `document_hash`.

## Folder layout

```
DATA_DIR/
  documents.json            # JSON array, one row per ingested document
  <document_hash>/
    source.pdf                # copy of the ingested file
    pages/
      page_001.md ...         # one file per PDF page, LLM transcription
      page_001.error          # present only if the page failed after retries
    tariff.md
    tariff_index.json
    charges.json
    classification.jsonl      # one row per classified section, the step 3 cache
    profile.json              # the step 4 response, cached
    manifest.json             # per step: prompt version, prompt sha, model, timestamps
```

No path is ever persisted. Both phases derive every location from `DATA_DIR` and the
document hash, so the data directory can move and a clone still works.

## Step 1: PageTranscriber (LLM, one call per PDF page)

**Input.** A single-page PDF produced by splitting the source with pypdf, sent as an
inline `application/pdf` part together with the prompt. Page index (1-based) is known
to the code.

**Prompt outline.**

- You are transcribing one page of a port tariff book into Markdown. Transcribe, do not
  summarise, do not omit anything, do not add commentary.
- The page may contain two logical pages side by side. Transcribe the left one fully,
  then the right one. Before each, emit `<!-- printed-page: N -->` using the page number
  printed in that logical page's footer, or `<!-- printed-page: unknown -->`.
- Every section heading that carries a number (like `3.3 PILOTAGE SERVICES` or
  `4.1.1 PORT DUES`) becomes a Markdown heading with the number and title exactly as
  printed. Heading level follows the depth of the number (`#` for `3`, `##` for `3.3`,
  `###` for `3.3.1`). Unnumbered headings become bold text, not headings.
- Tables become GitHub-flavoured Markdown tables with one header row. Where the source
  spans a header over several columns or merges cells, repeat the value in every cell it
  covers so each row is self-contained. Keep `n/a` cells. Keep the column order.
- Numbers are copied exactly as printed, including thousands separators. Dotted leader
  lines between a label and its amount become a single table row or `label: amount`.
- Keep conditions, bullet lists, footnotes and surcharge text with the section they
  belong to.

**Output.** Markdown text, written to `pages/page_NNN.md`. The code prepends
`<!-- pdf-page: NNN -->` to each file when joining.

**Joining.** `tariff.md` = concatenation of all page files in order, each preceded by its
`<!-- pdf-page: NNN -->` marker and followed by a blank line.

**Concurrency and failures.** Bounded parallelism (default 4) with exponential backoff
on 429 and 5xx. A page that still fails is recorded as `pages/page_NNN.error` with the
last error, and ingestion stops with a message listing the failed pages. Re-running
`ingest` retries only missing pages.

## Step 2: Index builder (code only)

**Input.** `tariff.md`.

**Algorithm.**

1. Walk the file line by line, tracking the current `pdf_page` and `printed_page` from
   the markers.
2. A Markdown heading whose text carries a section number starts a new node. The number may
   be introduced by a word naming the division, as in `SECTION 3` or `Chapter 2`, and the
   title may be absent because the document prints it separately underneath; the node then
   keeps the words as printed for its title and the real title stays in its text. Depth
   always comes from the dotted number, never from the number of `#` characters, so a
   mis-levelled heading cannot corrupt the hierarchy.
3. All following lines until the next numbered heading are that node's `text` (markers
   stripped). Text before the first numbered heading goes to a synthetic node with
   `id = "front-matter"`, `title = "Front matter"` (definitions and general notes live
   there).
4. If an id appears again later (a section continued on the next page and the
   transcription repeated the heading), append the text to the existing node instead of
   creating a duplicate.
5. `parent` = the id with the last dotted component removed, if such a node exists;
   otherwise the longest existing prefix; otherwise `null`. `children` is filled from the
   parents.
6. Nodes keep document order via an `order` integer.

**Output.** `tariff_index.json`:

```json
{
  "document_hash": "3f9a...",
  "sections": [
    {
      "id": "3.3",
      "title": "PILOTAGE SERVICES",
      "parent": "3",
      "children": ["3.3.1"],
      "order": 41,
      "pdf_page": 7,
      "printed_page": 13,
      "text": "Pilotage is compulsory at the Ports of ..."
    }
  ]
}
```

`printed_page` is `null` whenever the marker said `unknown`, was missing or did not parse as
an integer. It is never inferred from `pdf_page`, which would encode this book's two-up
layout (ADR-015). `pdf_page` is always known, because the joiner writes that marker itself.

**Helpers** (pure functions over the loaded index, unit-tested):

- `get_node(id)`
- `page_citation(id)`: the printed page number when there is one, otherwise a reference to
  the PDF page. This is what an answer cites.
- `get_with_children(id)`: the node's text followed by all descendants' text in document
  order, each prefixed with its heading.
- `get_context(id)`: the texts of the node's ancestors (root first) followed by
  `get_with_children(id)`. This is what the query phase hands to the agent so that
  general terms in a parent section (working hours, tonnage definitions) travel with the
  charge.

## Step 3: ChargeClassifier (LLM, one small call per section)

**Input per call.** The chain of ancestor titles (for example
`3 MARINE SERVICES > 3.3 PILOTAGE SERVICES`) and the node's own `text`. A node with no
children is always classified, however short its text. A node with children is skipped
without a call when it carries no content of its own, measured after stripping page
markers, table rule lines and blank lines (ADR-016). The front-matter node is classified
like any other.

**Question.** Does this section define a fee, due or charge that someone has to pay? If
yes, name it, say who pays, and state in one sentence when it applies.

**Response schema.**

```json
{
  "defines_charge": true,
  "charge_name": "Pilotage Dues",
  "payer": "vessel",
  "applies_when": "Vessel enters or leaves a port where pilotage is compulsory",
  "ports_mentioned": ["Durban", "Richards Bay"]
}
```

- `payer` is one of `vessel`, `cargo_owner`, `other`.
- When `defines_charge` is false, the other fields are `null` or empty.
- `ports_mentioned` lists port names that appear in this section's text, used only to
  build the registry's `ports` list. One entry per individual place: a heading or column
  that groups several places is split into one entry each, never returned as a combined
  label (ADR-018). Code splits and normalises the values again before they reach the
  registry, so a model that ignores the instruction still produces a usable list.

Every classified section, positive or negative, is cached as one row in
`classification.jsonl` keyed by the prompt version, the prompt sha, the model and the sha of
the section text. Re-transcribing one page therefore re-classifies only the sections that
page changed. `charges.json` is derived from that file; it is never the cache itself.

**Output.** `charges.json` keeps only positive rows. `section_id` comes from the loop,
not from the model.

```json
{
  "document_hash": "3f9a...",
  "prompt_version": 1,
  "model": "gemini-2.5-flash",
  "charges": [
    {
      "section_id": "3.3",
      "name": "Pilotage Dues",
      "payer": "vessel",
      "applies_when": "Vessel enters or leaves a port where pilotage is compulsory"
    }
  ]
}
```

Concurrency and retry rules are the same as Step 1.

## Step 4: DocumentProfiler (LLM, one call) and the registry

**Input.** The Markdown of the first three PDF pages (cover, contents, definitions).

**Response schema.**

```json
{
  "issuer": "Transnet National Ports Authority",
  "title": "Port Tariffs, Twenty Third Edition",
  "currency": "ZAR",
  "valid_from": "2024-04-01",
  "valid_to": "2025-03-31"
}
```

Unknown values are stored as `null` and warned about. They are never widened to sentinel
dates: the registry is an audit record and must not state a validity the document never
gave. "Open range" is a selection-time meaning, `null` being unbounded on that side.

**Registry row** appended to `documents.json` by code:

```json
{
  "document_hash": "3f9a...",
  "source": "Port Tariff.pdf",
  "issuer": "Transnet National Ports Authority",
  "title": "Port Tariffs, Twenty Third Edition",
  "currency": "ZAR",
  "valid_from": "2024-04-01",
  "valid_to": "2025-03-31",
  "ports": ["Cape Town", "Durban", "East London", "Mossel Bay", "Ngqura",
            "Port Elizabeth", "Richards Bay", "Saldanha"],
  "page_count": 27,
  "ingested_at": "2026-09-19T15:40:00Z",
  "active": true
}
```

`ports` is the sorted union of `ports_mentioned` across all classified sections, positive
and negative alike, normalised per ADR-018: combined labels split, trimmed, whitespace
collapsed, capitalised per word and de-duplicated. `active` defaults to true and can be
flipped by a future CLI command to retire a document.

## Update rule

A new edition is a new hash: new folder, new row. Rows are never overwritten or
deleted by ingestion. Selection between rows happens at query time (see
`query.md`, document selection).

## Non-goals

- No embeddings or vector store (ADR-013).
- No structured rule extraction at ingestion time (ADR-007). Rules are interpreted by the
  agent from section text at query time.
