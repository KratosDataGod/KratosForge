# Mutation testing, batch 206

A boundary re-score of three modules first scored before `mutate.py` made boundary mutants (`<` to `<=`, `>` to `>=`; see batch 36). Only the boundary mutants were run (`--kinds boundary`), so the scores below count those alone; each module's point score from its first batch stands as it was. The modules, with the batch that first scored each:
- This PC's schedule locks: a PID of 0, a takeover guard and a stale lock at their ages, and a stopped run's start time (`schedule_service.py`, a boundary re-score of batch 12).
- The SQL formatter (`sql_format.py`, a boundary re-score of batch 9).
- A notebook's resource files (`notebook_resources.py`, a boundary re-score of batch 4).

The run was at `main` bdf2c177 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran; each "after" run re-checked only the survivors, with `--only`. The tests given were each module's test files from its earlier batches, as paths relative to `sidecar/`, and they first passed once on the unmutated copy of `sidecar/`. Tests that read `docs/` or `examples/` fail in that copy, so they were left out with `--deselect`: `test_tsql_loud_gaps.py`'s three tests that read the refused list in the docs, for `sql_format.py`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `schedule_service.py` | test_schedules, test_schedule_locks_and_lists, test_schedule_service_edges, test_signed_out_schedules, test_fabric_pipeline_runs, test_fabric_connections, test_notebook_runs, test_notebook_batch, test_pipeline_schedules, test_cli_local_commands, test_integrations_scope_edges, test_integrations_workspace_scope, test_risk, test_schedule_service_mutation (+ test_schedule_service_boundary) | 4 of 4 | 0 / 4 | **0.0%** | 4 / 0 | **100%** | none |
| `sql_format.py` | test_sql_format_edges, test_sql_editor, test_snowflake_sql_variadic, test_tsql_contract, test_tsql_loud_gaps, test_code_format, test_sql_format_mutation, test_tsql_formatting, test_tsql_format_mutation | 3 of 3 | 3 / 0 | **100%** | no change needed | **100%** | none |
| `notebook_resources.py` | test_notebook_resources, test_notebook_resources_edges, test_builtin_links, test_notebook_resources_mutation, test_notebook_resources_service_mutation, test_notebook_resources_calls | 3 of 3 | 3 / 0 | **100%** | no change needed | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **schedule_service.py**: a PID of 0 counts as running without asking the system (on Linux `os.kill(0, 0)` succeeds, so the test stands in for it); a takeover guard exactly a minute old is still someone deciding; a live run's lock exactly `STALE_LOCK_SECONDS` old is kept; a stopped run's start time of exactly 40 characters is kept. → `test_schedule_service_boundary.py`.

### Left

None.

