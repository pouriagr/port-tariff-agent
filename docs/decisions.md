# Decisions

Short architecture decision records. Newest at the bottom. When a decision changes,
add a new ADR that supersedes the old one; do not edit history.

Format: context in one or two lines, the decision, why, and what was rejected.

---

## ADR-001: Vision-based page-by-page transcription

**Decision.** The PDF is split into single pages with pypdf and each page is sent to
Gemini as a PDF part. The model returns clean Markdown with proper tables and numbered
headings. Pages are cached individually and joined into `tariff.md`.

**Why.** The source is a two-column, 2-up spread produced by MS Publisher (see
`spec/pdf-notes.md`). Text extraction with pdftotext interleaves the two columns line by
line and destroys merged table cells. Fixing that in code would be brittle and specific
to this document, which contradicts the generality requirement. Per-page calls keep
outputs short (quality), give exact page numbers (citations), allow parallel calls and
single-page retries.

**Rejected.** pdftotext or pdfplumber with column splitting by x coordinate (document
specific); Docling or similar layout tools (extra dependency, still weak on merged
cells); the whole PDF in one call (long output degrades and cannot be retried in part).

## ADR-002: Section index built with code, not LLM

**Decision.** `tariff_index.json` is produced by a regex pass over the numbered Markdown
headings in `tariff.md`. Hierarchy comes from the section numbering. The list is flat;
`children` and `parent` hold ids only; each node's `text` is its own text up to the next
heading.

**Why.** Deterministic, free, testable, and the transcription prompt guarantees numbered
headings. A flat list makes lookup by id trivial and avoids duplicating text in nested
structures. Parent and general-terms text is assembled on demand by a helper.

**Rejected.** Nested tree objects (harder lookup, duplicated text); asking the LLM to
segment the document (cost and non-determinism for no gain).

## ADR-003: Charge catalog through one small call per section

**Decision.** `ChargeClassifier` is called once per section node with the ancestor titles
and that node's own text, and answers whether the node defines a payable charge, its
name, payer and a one-line applicability condition. Code keeps the positive rows as
`charges.json`.

**Why.** The user ruled out a single call over the whole document: cheap Gemini models
are not reliable on 30k-token inputs. A per-section yes/no question is a classification
task that small models handle well, runs in parallel, and confines any error to one
section. Total token cost is the same as one big call.

**Rejected.** One call over the full document; a per-charge hard-coded list.

## ADR-004: `section_id` is the charge key; no confidence field

**Decision.** Charges are identified by the section id of the section that defines them.
There is no separate `charge_id` and no `confidence` field.

**Why.** Section ids are unique by construction and trace straight back to the document.
Slugs derived from names collide (two "Port Dues" sections exist). A model-produced
confidence number carries no information we would act on.

**Rejected.** LLM-chosen ids (inconsistent); name slugs with de-duplication logic.

## ADR-005: One folder per document hash plus a registry

**Decision.** Ingestion output lives in `data/<sha256 of the PDF>/`. A root
`data/documents.json` holds one row per ingested document with issuer, title, currency,
validity period, covered ports and `ingested_at`. Ingestion is skipped when the hash is
already registered unless `--force` is given.

**Why.** New editions get new hashes, so nothing is overwritten and historical calls can
be priced with the tariff that was valid at the time. The registry is what maps a port
and a date to a folder.

**Rejected.** One folder per port (a document covers many ports); overwriting on update.

## ADR-006: Document selection rule

**Decision.** Given a port and an arrival date, select rows whose `ports` contain the port
and whose validity period contains the date. If several remain, the newest `ingested_at`
wins. If no date is given, use today's date. If nothing matches, the tool returns an
explicit error and the agent tells the user.

**Why.** Historical correctness by default; a corrected re-issue with the same validity
period supersedes the earlier one because it was ingested later.

## ADR-007: A single ReAct agent with two knowledge tools

**Decision.** The query phase is one `TariffAgent` in a ReAct loop with full chat history.
It has two knowledge tools, `get_charges` and `calculate`, plus a control tool
`submit_answer` that ends the loop with a validated answer. There is no separate query
parser, no structured rule schema and no rule cache.

**Why.** The user preferred the simpler design: the agent extracts what it needs from the
user's message when it fills the tool arguments, reads the relevant section texts, and
interprets them with chain-of-thought. It keeps the "agent finds and interprets the
rules" behaviour visible, supports follow-up questions naturally, and removes an entire
layer of schema design that would have to anticipate every tariff structure.

**Rejected.** A multi-stage pipeline (QueryParser to VesselCall, ChargeSelector,
RuleExtractor producing a structured rule, deterministic evaluator over that schema): more
deterministic but over-engineered and brittle to unfamiliar tariff structures.

## ADR-008: The LLM never does arithmetic

**Decision.** The agent writes each charge as an explicit expression, for example
`2 * (18608.61 + ceil(51300 / 100) * 9.72)`, and calls `calculate`. The tool evaluates a
whitelisted AST (numbers, `+ - * /`, parentheses, `ceil`, `floor`, `round`, `min`,
`max`) and returns the result.

**Why.** Accuracy is the first evaluation criterion. Multi-step decimal arithmetic across
six charges is exactly where cheap models slip. Translating "per 100 tons or part
thereof" into `ceil(gt / 100)` is a language task the model does well; the multiplication
is not. The recorded expression also makes every amount auditable.

**Rejected.** Chain-of-thought arithmetic in the answer; Gemini code execution (adds a
dependency on a hosted sandbox and hides the formula in generated code).

## ADR-009: Fixed JSON output plus a free-text answer

**Decision.** The final answer is a `TariffAnswer` object: a free-text `answer` for the
chat, and fixed fields for the charges (name, section id, page, formula, amount,
assumptions), total, currency and notes. Follow-up turns may leave `charges` empty.

**Why.** Numbers must be structured for the API, for tests against the ground truth and
for tables. The chat still needs prose. Both, not one.

## ADR-010: Raw `google-genai` SDK, no agent framework

**Decision.** The ReAct loop, tool dispatch and history handling are written directly on
the `google-genai` SDK's function calling.

**Why.** The loop is about fifty lines. Keeping it explicit shows the engineering rather
than hiding it behind framework abstractions, and avoids heavy dependencies.

**Rejected.** LangGraph (heavier, more ceremony), pydantic-ai (an extra abstraction layer
for little gain here), LangChain and LlamaIndex.

## ADR-011: Flash models by default, names in config

**Decision.** All extraction and selection calls use a Flash-class model. The agent also
uses Flash by default and can be switched to a Pro model through
`GEMINI_MODEL_AGENT`. Model names are never in code.

**Why.** Free-tier friendly and fast. The verified key can call `gemini-2.5-flash`,
`gemini-2.5-pro` and the newer `gemini-3.x` Flash models, so the exact default is chosen
in Phase 1 after comparing transcription quality on the two-column pages.

## ADR-012: `uv` for packaging and environments

**Decision.** `uv` manages the virtual environment, dependencies, lock file and scripts.
`pyproject.toml` plus `uv.lock` are committed.

**Why.** One fast tool for everything, reproducible installs, clean Docker builds.

## ADR-013: No vector store for now

**Decision.** Retrieval is by section id over `tariff_index.json`. There is no embedding
index.

**Why.** The document has 54 logical pages and the charge catalog already tells the agent
which sections to read. A retrieval layer over the same index can be added for very
large tariff books without changing the rest of the design.

## ADR-014: English only

**Decision.** All repository content, code comments, commit messages and assistant replies
are in English.

**Why.** The deliverable is reviewed by an English-speaking team.
