# Spec: Notes on the reference PDF

Facts about `data/raw/Port Tariff.pdf` that shaped the ingestion design. Update this
file when transcription problems are found in Phase 1.

## Physical structure

| Property | Value |
|---|---|
| Producer | Microsoft Publisher for Microsoft 365 |
| PDF pages | 27, landscape A4 |
| Logical pages | 54 (each PDF page is a 2-up spread: two printed pages side by side) |
| Printed page numbers | in the footer of each logical page, for example `15` and `16` on PDF page 8 |
| Tagged | yes |
| Fonts | ZapfDingbats and ArialUnicode referenced, missing locally (bullets render as boxes in some tools) |

## Why plain text extraction fails

`pdftotext -layout` reads across both logical pages, so every output line contains the
left page's text followed by the right page's text. Section 3.3 (pilotage) and the
surcharge bullets of section 3.6 (towage), which sit on facing pages, come out interleaved
line by line. Table rows with multi-line headers ("50 001 to 100 000 / Plus / Per 100
tons or part thereof above 50 000") lose their alignment; empty and `n/a` cells shift
values into the wrong port column. This is the reason for ADR-001 (vision-based
transcription).

## Table shapes the transcriber has to handle

- Port-column tables with 5 to 7 columns (Richards Bay, Durban, East London,
  Port Elizabeth / Ngqura, Mossel Bay, Cape Town, Saldanha), some with an "Other Ports"
  fallback column instead of a Durban column.
- Banded tables by vessel tonnage where each band has a base fee row and an incremental
  "Plus per 100 tons above the band start" row.
- Two-column header cells spanning two lines ("Port Elizabeth / Ngqura").
- Dotted-leader price lists ("Minimum fee .............. 235.52").
- `n/a` cells (Mossel Bay has no large-tonnage rates).

## Content map (for orientation only; the code discovers this itself)

Section 1 light dues; Section 2 VTS; Section 3 marine services (pilotage 3.3, tugs 3.6,
berthing 3.8, running of vessel lines 3.9); Section 4 port fees on vessels (port dues
4.1.1, berth dues 4.1.2, small vessels 4.2); Section 5 licences; Section 6 dry docks;
Section 7 cargo dues; Section 8 business processes. Definitions and general terms are in
the front matter and in section 3.1.

## Transcription findings (Phase 1 run, 2026-09-19)

Model: newest 3.x Flash (ADR-017), one call per PDF page, prompt version 1. The run produced
27 page files, 98 sections and 66 charges. What the eyeball check found:

| Area | Result |
| --- | --- |
| Two-up spreads | Correct. Both logical pages are transcribed left then right, each preceded by its own printed-page marker. No interleaving. |
| Printed page numbers | Every section carries one; none came back `unknown`. |
| Port-column tables | Intact, including the seven-column tug table and the merged `Port Elizabeth / Ngqura` header cell. |
| Fallback column | Preserved. Section 3.8 has no Durban column and keeps `Other Ports`, which is where its rates have to be read from. |
| Banded tonnage rows | Each band's base row and its `Plus per 100 tons` row stay aligned under one header row. The `Plus` label arrives as `Plus<br>Per 100 tons or part thereof`. |
| `n/a` cells | Preserved verbatim. |
| Thousands separators | Kept as printed, with a non-breaking-style space: `73 118.07`, not `73118.07`. The agent must strip the space before computing. |
| Encoding | Clean UTF-8. Em dashes, curly quotes and bullets survive; nothing is mangled. |
| Ground-truth sections | All six (1.1.1, 2.1.1, 3.3, 3.6, 3.8, 4.1.1) exist in the index, are classified as charges, and contain their expected constants verbatim. |

Two prompt-level issues were found and fixed rather than worked around in code:

- The charge classifier returned any place name it saw, so dry dock names, a country and an
  adjectival form of it reached the registry's `ports` list. The prompt now asks only for
  ports of call and explicitly excludes countries, regions, authorities, berths, terminals
  and facilities inside a port. Prompt version raised to 2, which re-runs that step alone.
- Three sanity checks fired on shapes that are not faults: a contents page repeating section
  numbers, a page that continues a section without starting one, and a section holding two
  tables of different widths. The checks were narrowed; the transcription was left alone.

### Structural findings from the same run

- **Top-level headings carry no title.** The book prints `SECTION 3` on its own line with
  `MARINE SERVICES` in bold underneath. The first index build therefore produced no
  top-level nodes at all, and every `3.x` section became a root. The builder now accepts a
  heading whose number is introduced by a word, so all eight top-level sections exist and
  the charges hang off them correctly. Builder version raised to 2, which rebuilds the index
  and reclassifies only what changed, without re-transcribing.
- **General terms sit in a numbered sibling, not in an ancestor.** Working hours, the
  out-of-hours surcharge rule and the tonnage definition live in section 3.1, which defines
  no charge of its own. It is therefore absent from `charges.json`, and because it is a
  sibling rather than an ancestor of 3.3, 3.6 and 3.8, `get_context` on those sections does
  not carry it, although the agent needs those terms to decide that no out-of-hours surcharge
  applies. **Resolved in Phase 2 by ADR-019**: the ChargeSelector is shown the sections that
  define no charge as well, and returns the ones whose terms are needed as `context_sections`,
  which `get_charges` returns beside the charges. No tariff-specific knowledge is involved.
- **A long price list stays as body text.** Under 4.3.1 the document prints dotted-leader
  items numbered `4.3.1.1`, `4.3.1.2` and so on, which the prompt turns into `label: amount`
  lines rather than headings. They remain inside their parent section, so their rates are
  still reachable through it, and the parent is classified as a charge. Left as is.
- **Remaining warnings are expected.** `UNPARSED_HEADING` counts the contents pages and the
  price-list items above; `EMPTY_SECTION` names three headings the document leaves empty;
  `SIBLING_NUMBER_GAP` names one number the document skips; `NUMERIC_LEAF_NOT_A_CHARGE`
  names the general-terms and definition sections, which is correct.
