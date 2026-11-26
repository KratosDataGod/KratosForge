# Mutation testing, batch 43

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The parity matrix: frozen cells and areas, the check that a familiar look needs a built-in skin, and the exact text of the tables and notes on the docs page (`parity.py`).
- Data Wrangler: the row, preview, step and split limits, which cast a column to a date gets, a sampled preview's first row, and each step's comment in the written cell (`wrangler.py`).
- Capacity and cost measurements: unit errors, time offsets, the 100% utilization limit, period checks, rounding, the stable id, freshness, and the wording of an incomplete total (`measurement.py`).

The run was at `main` c454d6d6b (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. Two tests in `test_parity.py` read `docs/` (`test_docs_page_matches_the_code` and `test_a_sql_rows_local_claim_follows_its_function_list`), so they were deselected from the parity runs; the "before" score for `parity.py` is lower than what the whole suite pins.

The new tests write nothing outside pytest's temporary folder and use no network or timing. The parity tests swap in their own platforms and areas, and the measurement freshness tests pass a fixed "now".

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `parity.py` | test_parity (two docs tests deselected), test_parity_edges (+ test_parity_mutation) | 61 of 61 (60 + 1) | 49 / 12 | **80.3%** | 49/60 → 57/60 | 0/1 → 1/1 | 58 / 3 | **95.1%** | 3 equivalent |
| `wrangler.py` | test_wrangler, test_wrangler_parity, test_outputs_hardening (+ test_wrangler_mutation) | 150 of 208 (147 + 3) | 140 / 10 | **93.3%** | 139/147 → 146/147 | 1/3 → 3/3 | 149 / 1 | **99.3%** | 1 equivalent |
| `measurement.py` | test_measurement, test_advisor, test_calibration, test_risk, test_scorecard_cost, test_usage_import, test_workload_capacity_use and the earlier calibration, costs, risk, scorecard, usage-reconcile, opportunities-impact and boundary mutation files (+ test_measurement_mutation) | 150 of 155 (145 + 5) | 132 / 18 | **88.0%** | 129/145 → 144/145 | 3/5 → 5/5 | 149 / 1 | **99.3%** | 1 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **parity.py**:
  - `Cell` and `Area` being frozen: changing either after it is made raises.
  - The skin check: a missing or unassessed look is accepted with no built-in skin, and a partial one is refused.
  - The docs table's exact text: one shared "Every area" note only when two or more areas of a platform have the same cell with a note, a platform with one area keeping its own note, a live run shown after the note, and a live run with no note still getting its line.
  - → `test_parity_mutation.py`.
- **wrangler.py**:
  - A result of exactly 1,000,000 rows previewed whole and one of 1,000,001 sampled; a sampled preview starting at the first row.
  - A preview of 100 rows.
  - 50 steps run and 51 refused.
  - A split into 2 or 20 parts taken, and 1 or 21 refused.
  - A timestamp column changed to a date by a cast, not by parsing text.
  - Each step's description written as its comment in the cell.
  - → `test_wrangler_mutation.py`.
- **measurement.py**:
  - An unknown unit named in the error whichever side it is on.
  - A time with its own offset keeping it.
  - A utilization of exactly 100% valid, and 100.5% refused.
  - A period whose start or end alone isn't a time refused.
  - Values, converted values and totals rounded to six places.
  - The id pinned as a fixed hash of 16 hex characters.
  - Freshness: no collection time is never stale, an observation exactly at its maximum age is fresh, and an age is never below zero.
  - An unknown measurement changing unit without a value.
  - The incomplete-total reason: "1 of 1 part" and "1 of 2 parts".
  - → `test_measurement_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `parity.py` | 227, 228, 229 | `areas[0]` → `areas[1]` (3 mutants) | Equivalent: these are reached only when a platform has more than one area, and a level is shared only when every area's cell equals the first one's, so the first and second areas give the same cell. |
| `wrangler.py` | 71 | `return "text"` → `return None` in `_kind` | Equivalent: `_literal` has no text branch, so a text column and a column of unknown kind both fall through to the same `repr(str(text))`. |
| `measurement.py` | 55 | `_STRENGTH` unknown 2 → 3 | Equivalent: only the order of the strengths matters, and 3 keeps unknown the weakest. |

