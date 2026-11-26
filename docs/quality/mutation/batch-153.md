# Mutation testing, batch 153

A re-score of `orchestration/runner.py`, which batch 12 left at **94.7%**, on today's `main`. Batch 12 ran before boundary mutants, so this run uses `--kinds all` and counts them.

The run was at `main` 4ddd3662 (2026-11-18), with the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 419 mutants, so 150 were sampled. The first run used five files: test_pipeline_runner_edges, test_orchestration_runner_mutation, test_pipeline_runs, test_pipeline_script and test_pipeline_data. Its 14 survivors were then run again with batch 12's whole set: those files, plus test_pipeline_schedules, _service_edges, _definitions, _gotchas, _data_edges, _notebooks_edges, _script_edges, _fabric_parity, _adapters_edges, test_orchestration_definition_edges, test_orchestration_runs_edges, test_cli_tenant_commands and test_pipeline_notebooks. That set killed one, and the "before" figures include it. Nine mutants timed out in the first run and count as killed. As in batch 12, they are run loops that never end.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/runner.py` | the five files above; survivors with batch 12's whole set (+ test_orchestration_runner_rescore) | 150 of 419 (147 + 3) | 137 / 13 (9 timeouts) | **91.3%** | 137/147 → 139/147 | 0/3 → 3/3 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/runner.py**:
  - A skipped activity made no attempt.
  - A run refused for a parameter declared without an object still gives its record.
  - A wait that ends exactly at its deadline isn't a timeout, and a wait with no time left doesn't wait again.
  - A child run is stopped at its deadline exactly.
  - The boundary tests replace the runner's clock, so they take no real time.
  - → `test_orchestration_runner_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `orchestration/runner.py` | 561, 660, 710, 450, 408, 664 | as in batch 12: `_matches`' last `return False` to `None`, ForEach's cancel check `or` to `and`, a child's `depth + 1` to `- 1`, `getattr(adapter, "container", False)` to `True`, and `daemon=True` on activity and ForEach threads | Equivalent, for batch 12's reasons. |
| `orchestration/runner.py` | 805 | `daemon=True` to `False` on a data read's thread | Equivalent: the loop below waits for it to finish, so the flag matters only at interpreter exit. |
| `orchestration/runner.py` | 249 | `getattr(adapter, "can_execute", False)` to `True` | Equivalent: the registry refuses to register a local entry whose adapter can't run (`check_entry`), so the default is never used. |

