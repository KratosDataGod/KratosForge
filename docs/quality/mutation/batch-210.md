# Mutation testing, batch 210

A boundary re-score of three modules first scored before `mutate.py` made boundary mutants (`<` to `<=`, `>` to `>=`; see batch 36). Only the boundary mutants were run (`--kinds boundary`), so the scores below count those alone; each module's point score from its first batch stands as it was. The modules, with the batch that first scored each:
- A notebook's parameters and widget values for a run (`notebook_inputs.py`, a boundary re-score of batch 9).
- Local data projects' engines (`localdata/manager.py`, a boundary re-score of batch 5).
- Clearing up local writes: whose spill file is whose (`local_writes_service.py`, a boundary re-score of batch 1).

The run was at `main` bdf2c177 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran; each "after" run re-checked only the survivors, with `--only`. The tests given were each module's test files from its earlier batches, as paths relative to `sidecar/`, and they first passed once on the unmutated copy of `sidecar/`. Tests that read `docs/` or `examples/` fail in that copy, so they were left out with `--deselect`: `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` for `notebook_inputs.py`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_inputs.py` | test_notebook_inputs, test_notebook_inputs_edges, test_notebook_publish, test_prove, test_side_by_side, test_integrations_executions, test_side_by_side_edges, test_dbutils, test_dbutils_parity, test_notebook_inputs_mutation (+ test_notebook_inputs_boundary) | 1 of 1 | 0 / 1 | **0.0%** | 1 / 0 | **100%** | none |
| `localdata/manager.py` | test_local_data, test_local_data_calls, test_local_sql_stop, test_localdata_manager_mutation, test_localdata_manager_edges | 1 of 1 | 1 / 0 | **100%** | no change needed | **100%** | none |
| `local_writes_service.py` | test_local_writes_clear, test_local_writes_service_edges, test_retention, test_local_writes_service_mutation (+ test_local_writes_service_boundary) | 1 of 1 | 0 / 1 | **0.0%** | 1 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **notebook_inputs.py**: a value of exactly `MAX_VALUE` characters is allowed. → `test_notebook_inputs_boundary.py`.
- **local_writes_service.py**: a spill file named `write-<pid>` with nothing after the pid is from a build before spill files were named, so it has no owner. → `test_local_writes_service_boundary.py`.

### Left

None.

