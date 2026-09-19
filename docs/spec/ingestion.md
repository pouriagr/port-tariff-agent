# Spec: Ingestion

Turn one tariff PDF into a cached, machine-readable document that the query phase can
use without touching the PDF again. Runs once per document. Nothing here knows anything
about a specific port or charge.

Related ADRs: 001 to 006, 011.

## Trigger and idempotency

- Entry point: `port-tariff ingest <pdf> [--force]` (and `POST /documents` later).
- Compute `document_hash = sha256(pdf bytes)`.
- If `documents.json` already has a row with that hash and `--force` is not set: print the
  existing row and stop.
- Output folder: `DATA_DIR/<document_hash>/`.
- Every step writes its output to disk before the next step starts, so a crash resumes
  from the last completed step. Step outputs carry a `prompt_version` where an LLM was
  involved; a changed prompt version invalidates that step's cache.

## Folder layout

```
DATA_DIR/
  documents.json
  <document_hash>/
    source.pdf                # copy of the ingested file
    pages/
      page_001.md ...         # one file per PDF page, LLM transcription
      page_001.error          # present only if the page failed after retries
    tariff.md
    tariff_index.json
    charges.json
```

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
2. A line matching `^#{1,6}\s+(\d+(?:\.\d+)*)\s+(.+?)\s*$` starts a new node with
   `id = group 1`, `title = group 2`.
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

**Helpers** (pure functions over the loaded index, unit-tested):

- `get_node(id)`
- `get_with_children(id)`: the node's text followed by all descendants' text in document
  order, each prefixed with its heading.
- `get_context(id)`: the texts of the node's ancestors (root first) followed by
  `get_with_children(id)`. This is what the query phase hands to the agent so that
  general terms in a parent section (working hours, tonnage definitions) travel with the
  charge.

## Step 3: ChargeClassifier (LLM, one small call per section)

**Input per call.** The chain of ancestor titles (for example
`3 MARINE SERVICES > 3.3 PILOTAGE SERVICES`) and the node's own `text`. Nodes whose own
text is shorter than 40 characters are skipped without a call (pure container headings).
The front-matter node is classified like any other.

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
  build the registry's `ports` list.

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

Unknown values are `null`; the code then falls back to an open validity range and warns.

**Registry row** appended to `documents.json` by code:

```json
{
  "hash": "3f9a...",
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

`ports` is the sorted union of `ports_mentioned` across all classified sections,
normalised by trimming and title-casing. `active` defaults to true and can be flipped by
a future CLI command to retire a document.

## Update rule

A new edition is a new hash: new folder, new row. Rows are never overwritten or
deleted by ingestion. Selection between rows happens at query time (see
`query.md`, document selection).

## Non-goals

- No embeddings or vector store (ADR-013).
- No structured rule extraction at ingestion time (ADR-007). Rules are interpreted by the
  agent from section text at query time.
