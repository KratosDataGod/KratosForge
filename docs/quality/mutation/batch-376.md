# Mutation testing, batch 376

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The capacity and FinOps controls from imported Capacity Metrics exports, and the record they read (`excellence/catalog_capacity.py`, FEA-202).

The run was at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 179 of 179 mutants ran (167 point + 12 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_excellence_capacity, test_excellence_catalog and test_excellence, about 5 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_capacity.py` | test_excellence_capacity, test_excellence_catalog, test_excellence (+ test_excellence_capacity_mutation) | 179 of 179 (167 + 12) | 128 / 51 | **71.5%** | 127/167 → 167/167 | 1/12 → 12/12 | 179 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_capacity.py**:
  - A CU value counts only as a number of zero or more (not text, not True), and an operation only with both CU and a start.
  - CU seconds are rounded to three places and shares to four, in the operations, the Compute-page periods and each control's detail.
  - Refreshes are counted per workspace, item and day, tied counts sort by name, twenty are kept, and the days over 24 are counted before that cut (24 isn't over).
  - A Compute-page import needs a period that ends after it starts (not at it) and some CU, and keeps its id. The record lists its imports in id order, with each one's id, layout and row count.
  - Overlapping Compute-page periods count once, and periods that only touch both count.
  - Each threshold at its exact value: 10% failed work fails, 10% utilization passes, a window of exactly a day can pass, exactly 7 days of Compute-page periods is enough, and F1 is a paid size while F0 isn't.
  - Exact wording: CU-hours to a tenth, days, the window's dates, five refreshed items named, the refreshes over 24 in the benefit, the unused capacity, an unknown size or state, and "no CU" not evaluated.
  - → `test_excellence_capacity_mutation.py`.
