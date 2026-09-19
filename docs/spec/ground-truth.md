# Spec: Ground truth and validation

Reference case supplied with the task, reverse-engineered against the tariff document
on 2026-09-19. This file is for tests and the accuracy report. Production code never
reads it.

## Reference vessel call

Vessel SUDESTADA, bulk carrier, built 2010, flag Malta.

| Field | Value |
|---|---|
| Gross tonnage (GT) | 51,300 |
| Net tonnage | 31,192 |
| DWT | 93,274 |
| LOA | 229.2 m |
| Beam | 38.0 m |
| Port | Durban |
| Arrival | 2024-11-15 10:12 |
| Departure | 2024-11-22 13:00 |
| Days alongside | 3.39 |
| Activity | Exporting iron ore, 40,000 t, 2 operations, 7 holds |

Document: TNPA Tariff Book, April 2024 to March 2025 (`data/raw/Port Tariff.pdf`).
All amounts in ZAR, excluding 15 percent VAT.

## Expected values and their derivation

`u = ceil(GT / 100) = 513` is the "per 100 tons or part thereof" unit count.

| Charge | Section | Formula as read from the document | Computed | Expected | Deviation |
|---|---|---|---:|---:|---:|
| Light Dues | 1.1.1 | `u * 117.08` | 60,062.04 | 60,062.04 | 0.000 % |
| Port Dues | 4.1.1 | `u * (192.73 + 57.79 * days)`, days = 3.39 | 199,371.35 | 199,549.22 | -0.089 % |
| Towage Dues | 3.6 | `2 * (73118.07 + ceil((GT - 50000) / 100) * 32.24)` | 147,074.38 | 147,074.38 | 0.000 % |
| VTS Dues | 2.1.1 | `GT * 0.65` | 33,345.00 | 33,315.75 | +0.088 % |
| Pilotage Dues | 3.3 | `2 * (18608.61 + u * 9.72)` | 47,189.94 | 47,189.94 | 0.000 % |
| Running Lines | 3.8 | `2 * (2801.91 + u * 13.68)` | 19,639.50 | 19,639.50 | 0.000 % |

Notes on each:

- **Light Dues.** Flat rate per 100 tons for non-coastal vessels, charged once at the
  first South African port of call.
- **Port Dues.** Basic fee per 100 tons plus a per-100-tons, per-24-hour fee applied pro
  rata. The reference figure implies 3.396 days, so the task's `days_alongside` of 3.39
  was probably truncated. Using arrival to departure (7.12 days) would give 309,853.11,
  which does not match; the reference treats time in port as time alongside. No
  reductions apply: the vessel works cargo and stays longer than 12 hours.
- **Towage Dues.** Durban column, band 50,001 to 100,000 GT: basic fee plus an increment
  per 100 tons above 50,000. The fee is per service and is not multiplied by the number
  of tugs (the craft allocation table says 3 tugs for this band). Two services: arrival
  and departure.
- **VTS Dues.** Durban and Saldanha Bay rate of 0.65 per GT. The reference figure equals
  0.65 times 51,255, so it most likely contains a small error on the task authors' side.
- **Pilotage Dues.** Durban column: basic fee per service plus a rate per 100 tons. Two
  services.
- **Running Lines.** The reference figure matches section 3.8 Berthing Services, "Other
  Ports" column (Durban has no column of its own in that table), for two services.
  Section 3.9, literally titled "Running of vessel lines", gives 2 x 1,654.56 = 3,309.12
  and does not match. The agent computes both; the README explains the label mismatch.

Cross-cutting interpretation points the agent has to get right without being told:

- "per 100 tons or part thereof" means round up to the next 100 tons.
- Durban has its own column in some tables (pilotage, towage, VTS) and falls under
  "Other Ports" in others (berthing, running of lines).
- Movement-based services (pilotage, towage, berthing, running lines) are charged per
  service; a normal call has two: entering and leaving. The berthing text states that
  berthing and unberthing are two separate services.
- Durban is a 24-hour port, so no out-of-hours surcharge applies.
- Tonnage for tariff purposes is gross tonnage per the 1969 Tonnage Convention.

## Test specification (Phase 3, implemented)

`tests/test_ground_truth.py`. The reference query is built once by `reference_query(port)`,
so the second port is provably the same question with one word changed. It states the
vessel's facts and nothing about columns, services, rounding or which sections to read.

**How a run is reproduced.** A cassette (`tests/cassettes/durban_reference.json`) holds
what the model said, in one ordered queue covering both call sites, with the thought
signatures. Replay recomputes document selection, section texts and every amount from the
committed artifacts through the real code, so the loop, the toolbox and the evaluator are
all exercised; only the model is stubbed (ADR-023). Offline by default, so CI needs no key.
`uv run pytest -k record --live` re-records, and the recorder asserts the same values the
replay does, so a bad recording fails at record time.

**Accuracy assertions.**

- Charges are summed per `section_id` before comparison, since one section can be billed
  as more than one line. Each of the six is its own parametrized case.
- Each is within 0.5 percent of the expected value. The tolerance exists for the two
  reference-side errors in 2.1.1 and 4.1.1.
- A second assertion pins the *other four* to 0.05 percent, by asserting that the set of
  sections deviating by more than that is exactly `{2.1.1, 4.1.1}`. Without it a real
  regression could hide inside the tolerance.
- Extra charges such as 3.9 are allowed and printed, but must resolve in the index and
  pass the provenance check below. The recorded run priced none.
- Self-consistency: the total equals the sum of the lines (an amount computed in prose
  would not), the currency is the registry row's, `missing_inputs` is empty, and every
  charge's `page_citation` is the one the index gives for that section.

**Generality assertions** (`tests/provenance.py`, ADR-024). Same vessel at Cape Town, no
code changed. No expected values exist, so nothing asserts an amount:

- *Provenance.* Every two-decimal literal in a formula, minus the numbers stated in the
  question, appears verbatim in the text of the cited section. Subtracting the question's
  numbers excludes the tonnage and the days alongside; the two-decimal rule excludes the
  100 of "per 100 tons", the band floor and the number of services.
- *Column choice.* Where the cited section's table has a column naming this port, the rate
  came from it; where the table has port columns but none for this port, the rate came
  from a column naming no port. A header "names a port" per the registry row's `ports`.
  This is the check that catches the wrong column of the right section, which provenance
  alone cannot.
- *Differentiation.* The two ports did not resolve to identical constants everywhere, and
  both priced the same set of sections.

Both checks also run against the Durban run. The recorded result: 2.1.1, 3.3, 3.6 and 3.8
differ between the two ports; 1.1.1 and 4.1.1 are nationwide and identical. Section 3.8 is
the case the fallback column exists for — Cape Town has its own column there, Durban does
not.

**Staleness.** Fatal: the cassette's recorded question, or the document hash the port now
resolves to, differing from the recording. A changed prompt only warns (ADR-023).
