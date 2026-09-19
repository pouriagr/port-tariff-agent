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
