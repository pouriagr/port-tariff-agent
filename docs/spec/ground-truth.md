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

## Test specification (Phase 3)

- Integration test `tests/test_ground_truth.py`: run the agent on the reference query,
  match returned charges to the table above **by `section_id`**, assert each amount is
  within 0.5 percent of the expected value, and assert that sections 1.1.1, 2.1.1, 3.3,
  3.6, 3.8 and 4.1.1 are all present.
- Tolerance is 0.5 percent because two reference values themselves deviate by about
  0.09 percent from the document's formulas.
- Section 3.9 may additionally appear in the output; the test does not fail on extra
  charges but reports them.
- The test runs against recorded LLM responses by default (offline); a `--live` marker
  runs it against the API.
- Generality check: same vessel at Cape Town. No expected values are available, so the
  test asserts only that the agent used Cape Town's column where one exists and the
  fallback column where it does not (checked via the formulas' constants against the
  section texts), and that no code changed.
