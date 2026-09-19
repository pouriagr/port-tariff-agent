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

**Why.** Free-tier friendly and fast. Superseded in part by ADR-017, which names the three
model roles and settles the defaults.

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

## ADR-015: Page citation survives an unknown footer

**Context.** The transcription prompt lets the model emit `<!-- printed-page: unknown -->` when a
footer is unreadable or absent, but the query phase cites a page for every charge.

**Decision.** A section node stores `printed_page: int | None`, never inferred, plus `pdf_page:
int`, which is always known because our own joiner writes that marker. `TariffIndex.page_citation`
returns the printed number when there is one and a PDF page reference otherwise. `get_charges`
returns all three fields and the answer schema carries `page_citation` as text rather than
`printed_page` as an integer.

**Why.** Deriving a printed number from the PDF page index would encode the assumption that this
book prints two logical pages per sheet, which is exactly the document-specific logic the brief
forbids. A PDF page number is a property of every PDF, so the fallback stays general and a reader
can always verify the citation by opening the file.

**Rejected.** Inferring the printed number arithmetically (document specific); storing a sentinel
integer (indistinguishable from a real page); failing ingestion on a missing footer (one unreadable
footer would block a whole document).

## ADR-016: The classifier skips containers, never leaves

**Context.** `spec/ingestion.md` originally skipped any section whose own text was shorter than 40
characters, on the assumption that such nodes are pure container headings.

**Decision.** A section with no children is always classified, however short its text. A section
with children is classified only when it carries content of its own, measured after stripping page
markers, table rule lines and blank lines.

**Why.** Forty is a number tuned by eye against one document, and a leaf whose rates live in a
table can easily have less prose than that. The two error directions are not symmetric: a needless
call costs one cheap request and a negative row, while a wrongly skipped leaf silently removes a
whole charge from the final answer. "Leaf versus container" is a property of any numbered document,
so it also generalises where a character count does not.

**Rejected.** Keeping the character threshold; measuring length including descendants' text (a
container would then always qualify and the saving disappears).

## ADR-017: Three model roles, three environment variables

**Decision.** Ingestion and the agent name three roles, each configured by its own variable and
never by a literal in code: `GEMINI_MODEL_EXTRACT` for page transcription and document profiling,
`GEMINI_MODEL_CLASSIFY` for per-section charge classification, and `GEMINI_MODEL_AGENT` for the
ReAct loop. The defaults documented in `.env.example` are the newest Flash model for extraction and
the agent, and the newest Flash-Lite model for classification. Supersedes the open question left in
ADR-011.

**Why.** Classification is one small call per section, so a document of this size costs a few
hundred requests, an order of magnitude more than every other step combined. Pointing that step at
a lighter model draws on a separate, larger free-tier quota pool and keeps a full ingestion inside
a day's limits, while transcription, which is the step that actually determines accuracy, keeps the
stronger model. A yes/no classification over one short section is exactly the task a lite model
handles reliably.

**Rejected.** One model for everything (either too slow and quota-hungry, or too weak for
transcription); batching several sections per classification call (contradicts ADR-003 and
reintroduces the long-input reliability problem it was written to avoid).

## ADR-018: Port labels are split on write and matched on a normalised key

**Context.** Document selection matches a requested port against the `ports` list in the registry,
and that list is the union of what the classifier reports per section. Tariff tables routinely name
several places in one column header.

**Decision.** Two layers, neither of which contains a port name. The classification prompt asks for
one entry per individual place and forbids combined labels. Code then splits every reported value
on the separators `/`, `,`, `;`, `&` and the word `and`, trims, collapses whitespace, capitalises
per word, de-duplicates and sorts. Matching uses a `port_key` function, applied symmetrically to
the stored value and to the port named in the query, which lowercases, collapses whitespace, strips
punctuation and drops a leading "port of".

**Why.** A merged label would enter the registry as a single string that matches neither of the
places it names, making a document unfindable for one of its own ports. Splitting on punctuation
and one English conjunction is general; a gazetteer of real port names would not be.

**Rejected.** Substring matching at query time (a short port name matches unrelated rows); cleaning
the list against a list of known ports (document specific); splitting on hyphens or periods (both
occur inside real place names).

## ADR-019: The charge selector also sees the sections that define no charge

**Context.** General terms — working hours, the out-of-hours surcharge rule, the tonnage
definition — live in a numbered section that defines no charge of its own. It is therefore absent
from `charges.json`, and because it is a sibling rather than an ancestor of the charge sections it
qualifies, `get_context` on those sections does not carry it. Without those terms the agent cannot
decide whether a surcharge applies.

**Decision.** The ChargeSelector's input is the charge catalog plus every other numbered section
that has text of its own, given as id, title and a short preview. Its response gains a second list,
`context_sections`, beside `applicable`. `get_charges` returns the full text of both, the charges
to be computed and the context to be read. Code drops ids that are not in the index, de-duplicates,
and removes from `context_sections` anything already in `applicable`.

**Why.** Which sections carry the general terms is a property of the document, not of the code. The
model already reads the whole catalog to pick charges; letting it also name the sections it needs
to interpret them keeps one decision in one place and adds no structural assumption. A section
whose terms live three levels away, or in a front-matter section, is found the same way.

**Rejected.** Code pulling in every childless non-charge sibling of an applicable section: a
structural heuristic that over-collects where a document groups charges tightly and under-collects
where the terms sit somewhere else. Always sending the whole document (blows the context window and
buries the relevant text).

## ADR-020: A provider-neutral tool-calling protocol

**Context.** The ReAct loop needs multi-turn function calling, which the client wrapper does not
yet offer; it only does single-shot structured output.

**Decision.** `llm/protocol.py` gains provider-neutral types — `UserMessage`, `ModelTurn`,
`ToolCall`, `ToolResult`, `ToolSpec` — and a `ToolCallingGenerator` protocol with one method,
`generate_with_tools`. `GeminiClient` translates those types to and from the SDK's `Content` and
`FunctionDeclaration`. The agent, its tools and its history never import the provider SDK.

A tool call also carries an opaque `signature`, which the client fills from the provider and hands
back with the call. A thinking model rejects a conversation whose earlier parts lost it, and the
agent never looks inside it.

**Why.** `llm/client.py` is the only module that imports `google.genai`, and that boundary is what
makes every other module testable offline against a fake. A loop built on SDK types would drag the
provider into the agent package, into the tests and eventually into the API layer. Keeping the loop
explicit over neutral types is also what ADR-010 buys by refusing a framework.

**Rejected.** Passing `types.Content` through the agent (couples the loop to the provider);
a second provider abstraction layer with adapters per provider (nothing needs it yet).

## ADR-021: Tool parameters are declared from the Pydantic models, with references inlined

**Context.** `submit_answer` takes a nested object: a list of charges, each with its own fields.
Pydantic's `model_json_schema()` expresses nesting with `$defs` and `$ref`, and a function
declaration accepts only a restricted subset of JSON Schema, of which references are the part
least reliably supported.

**Decision.** A helper, `llm/schema.py::json_schema_for`, renders a Pydantic model to a JSON schema
with every `$ref` inlined and the keywords the provider rejects removed. Every tool declares its
parameters through it, from the same model the handler validates against.

**Why.** One definition of each tool's arguments, used both to tell the model what to send and to
check what it sent. Hand-written schemas beside the models would drift.

**Rejected.** Taking the answer as a JSON string argument and parsing it in code: the model loses
the schema while composing the answer, which is exactly when it needs it. Flattening `TariffAnswer`
into scalar arguments (loses the per-charge structure).
