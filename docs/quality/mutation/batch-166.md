# Mutation testing, batch 166

A boundary re-score, as in batch 163, of six more modules first scored before batch 36. Only the boundary mutants were run (`--kinds boundary`); each module's point score from its first batch stands. The modules, with the batch that first scored each:
- Fabric runs from the Monitor page, Fabric steps' results and Direct Lake models (`cloud_service.py`, batch 16).
- Proofs and replay bundles (`prove_service.py`, batch 16).
- Databricks built-in functions in SQL (`databricks_builtins_sql.py`, batch 18).
- Local schedules: when a schedule fires next (`local_schedules.py`, batch 22).
- Reading a pipeline definition from a file, a Fabric Git folder or item parts (`orchestration/definition.py`, batch 22).
- Notebook clean-up suggestions (`cleanup.py`, batch 23).

The run was at `main` 4ddd3662 (2026-11-18) with batches 163 to 165's tests added, and the same method as batch 163. Tests that read files outside `sidecar/` were left out: two `test_uninstall_script.py` tests for `local_schedules.py`. For `prove_service.py`, `test_replay.py` and `test_kernel_process.py` were left out too: with them the unmutated run took over the 120 s limit, so no mutant could be scored.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cloud_service.py` | the 9 test files that use it (+ test_cloud_service_boundary) | 4 of 4 | 2 / 2 | **50.0%** | 3 / 1 | **75.0%** | 1 timing only |
| `prove_service.py` | test_prove, test_prove_service_edges, test_prove_service_mutation, test_local_retention_gaps (+ test_prove_service_boundary) | 4 of 4 | 1 / 3 | **25.0%** | 2 / 2 | **50.0%** | 1 timing only, 1 equivalent |
| `databricks_builtins_sql.py` | test_databricks_builtins_sql_mutation, test_databricks_table_statements (+ test_databricks_builtins_sql_boundary) | 5 of 5 | 4 / 1 | **80.0%** | 5 / 0 | **100%** | none |
| `local_schedules.py` | the 21 test files that use it (+ test_local_schedules_boundary) | 5 of 5 | 3 / 2 | **60.0%** | 4 / 1 | **80.0%** | 1 equivalent |
| `orchestration/definition.py` | the 12 test files that use it (+ test_orchestration_definition_boundary) | 4 of 4 | 1 / 3 | **25.0%** | 4 / 0 | **100%** | none |
| `cleanup.py` | test_cleanup, test_cleanup_edges, test_cleanup_mutation (+ test_cleanup_boundary) | 5 of 5 | 4 / 1 | **80.0%** | 5 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **cloud_service.py**: a table with no data files isn't called V-Ordered. → `test_cloud_service_boundary.py`.
- **prove_service.py**: a replay bundle of exactly the size limit opens. → `test_prove_service_boundary.py`.
- **databricks_builtins_sql.py**: `vector_norm` with degree 1 keeps it. → `test_databricks_builtins_sql_boundary.py`.
- **local_schedules.py**: a schedule that starts exactly now runs next one interval later; one that starts later runs then. → `test_local_schedules_boundary.py`.
- **orchestration/definition.py**: text, a file and a Git folder part of exactly the size limit are read. → `test_orchestration_definition_boundary.py`.
- **cleanup.py**: one empty cell among others is named; blank cells alone get no suggestion. → `test_cleanup_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `cloud_service.py` | 115 | `time.monotonic() > deadline` to `>=` while open runs are refreshed | Timing only: at exactly the deadline one more run is left unchecked. |
| `prove_service.py` | 861 | `time.monotonic() - started < MAX_FABRIC_SECONDS` to `<=` | Timing only: at exactly six hours the Fabric run is asked once more. |
| `prove_service.py` | 802 | `i < len(suffixes)` to `<=` | Equivalent: `_suffixes` has one entry per cell, so a cell's index is always below its length. |
| `local_schedules.py` | 119 | `nxt - now > step` to `>=` | Equivalent: at exactly one step, `now` is on the grid, and the grid search picks the same next time. |

