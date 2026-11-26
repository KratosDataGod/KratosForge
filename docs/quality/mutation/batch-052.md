# Mutation testing, batch 52

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- Where local pipeline run records live, and how a run left going by a stopped app is marked (`orchestration/runs.py`).
- What every usage provider gives the importer, and how a file's headers are matched to a layout (`usage/providers.py`).
- Azure Cost Management cost details and FOCUS exports (`usage/cost_management.py`).

The run was at `main` 13d7b79db (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out, and no `-k` filter was used.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing. `best_practice_checkers.py` was looked at first and left out, because it is part of governance's Check now.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/runs.py` | test_orchestration_runs_edges, test_pipeline_runs (+ test_orchestration_runs_mutation) | 34 of 34 (32 + 2) | 24 / 10 | **70.6%** | 24/32 → 32/32 | 0/2 → 2/2 | 34 / 0 | **100%** | none |
| `usage/providers.py` | test_usage_dates, test_usage_finite, test_usage_import, test_usage_reader_mutation, test_usage_reader_store_edges, test_usage_reconcile_mutation (+ test_usage_providers_mutation) | 34 of 34 (33 + 1) | 30 / 4 | **88.2%** | 30/33 → 33/33 | 0/1 → 1/1 | 34 / 0 | **100%** | none |
| `usage/cost_management.py` | the same usage files (+ test_usage_cost_management_mutation) | 71 of 71 (68 + 3) | 56 / 15 | **78.9%** | 56/68 → 68/68 | 0/3 → 3/3 | 71 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/runs.py**:
  - Trim keeps exactly the newest 200 records, and the list gives 50 by default and at least one.
  - A run id may be 64 characters and use every allowed character; 65 is refused.
  - Ids with other characters are refused (added after review; not a mutant the tool makes).
  - Recovery runs once per store: a run started after it is never marked interrupted by a later read, and a second call reports 0.
  - Runs this process is running are left as they are.
  - An interrupted run gets an end time, and keeps one it already had.
  - → `test_orchestration_runs_mutation.py`.
- **usage/providers.py**:
  - A layout can't be changed after it's made, and the base provider refuses to read rows.
  - When two layouts fit equally, the first one is kept.
  - A column with no header is never listed as left out.
  - → `test_usage_providers_mutation.py`.
- **usage/cost_management.py**:
  - A row with no billing currency, or whose period ends when it starts, is skipped with its reason.
  - A negative cost or quantity is a refund; zero and amounts under 1 are not.
  - Meter category, unit, charge type and pricing are kept as they are, and empty ones are none.
  - The capacity name comes from the resource id when the row has no name.
  - The duplicate-detection keys (`slot` and `key`) are pinned to their values. Earlier imports stored them, so a changed key would let the same file import twice.
  - → `test_usage_cost_management_mutation.py`.

