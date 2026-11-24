# Mutation testing, batch 41

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Running one query on two engines and comparing the answers: the default row limit, a side that fails or returns no table, which error a failed side quotes, and the refusals before anything runs, including a remote engine's result that needs the caller's copy confirm (`engines/compare.py`).
- Databricks DBUs billed, from `system.billing.usage`: the fixed query and its day choices, the row limit, which rows count as DBUs, the rounding, a quoted error's length, and the last read kept on this PC per workspace (`engines/databricks_billing.py`).
- Capacity settings history: the rows a read of capacities and of an administrator's overrides adds, and the read row's id and reason (`capacity_history.py`).

The run was at `main` fa9ed27b4 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

The new tests write only under pytest's temporary folder and use no network or timing. The compare tests use scripted sessions, and the billing tests a stand-in adapter, as the existing tests do.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/compare.py` | test_engine_compare (+ test_engine_compare_mutation) | 61 of 61 (60 + 1) | 51 / 10 | **83.6%** | 50/60 → 59/60 | 1/1 → 1/1 | 60 / 1 | **98.4%** | 1 equivalent |
| `engines/databricks_billing.py` | test_databricks_billing, test_engines_service_edges (+ test_databricks_billing_mutation) | 44 of 44 (43 + 1) | 28 / 16 | **63.6%** | 28/43 → 42/43 | 0/1 → 1/1 | 43 / 1 | **97.7%** | 1 equivalent |
| `capacity_history.py` | test_capacity_history (+ test_capacity_history_mutation) | 31 of 31 (31 + 0) | 26 / 5 | **83.9%** | 26/31 → 31/31 | none | 31 / 0 | **100%** | none |

`capacity_history.py` has no `<` or `>` comparisons, so it has no boundary points. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/compare.py**:
  - The default row limit of 1,000,000 given to both sides, and a limit of one row accepted.
  - A side's seconds rounded to four places.
  - A left side that is ok but returns no table: the right side doesn't run, and the problem says "returned no rows to compare", not "didn't give an answer".
  - A failed side quoting its last error, not its first, and "Error: " for an error with no name or value.
  - The refusal text: the engine's note added only when it has one.
  - A remote engine that can't return a result refused as unsupported, not as a missing copy confirm.
  - → `test_engine_compare_mutation.py`.
- **engines/databricks_billing.py**:
  - The query's `LIMIT 500`, the 500 result rows asked for, and no preview rows.
  - Each offered day count (1, 7, 30) taken and others refused.
  - A row with no job, pipeline, warehouse or cluster counted as "other" with no id.
  - Zero and fractional DBUs kept; NaN and negative DBUs each dropped.
  - DBUs rounded to three places and seconds to one.
  - A quoted error cut at 600 characters, and one of exactly 600 kept whole.
  - The per-workspace file named from the host with other characters as underscores, cut at 120 characters, and "workspace" for an empty host.
  - Saving making the folder and saving again over the last read.
  - → `test_databricks_billing_mutation.py`.
- **capacity_history.py**:
  - A read row's id: an administrator's read tagged `admin:`, a capacity read untagged, and a read with no capacity recorded under `-`.
  - A reason cut at 500 characters, and an empty reason kept as none.
  - An override row with no capacity id not taken as a capacity seen before.
  - → `test_capacity_history_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `engines/compare.py` | 99 | `assert ... and ...` → `or` | Equivalent: every part is already true at that point (the loop above returned otherwise), so the assert passes either way. |
| `engines/databricks_billing.py` | 87 | `round(total, 3)` → `round(total, 4)` | Equivalent: each row is already rounded to three places, so their sum differs from its three-place rounding only by float noise far below the fourth place. |

