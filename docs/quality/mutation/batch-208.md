# Mutation testing, batch 208

A boundary re-score of three modules first scored before `mutate.py` made boundary mutants (`<` to `<=`, `>` to `>=`; see batch 36). Only the boundary mutants were run (`--kinds boundary`), so the scores below count those alone; each module's point score from its first batch stands as it was. The modules, with the batch that first scored each:
- The redactor: a webhook URL in a message, and a message cut to its limit (`redact.py`, a boundary re-score of batch 1).
- The local data service's old query calls (`localdata_service.py`, a boundary re-score of batch 6).
- A full disk or memory, told plainly (`disk_full.py`, a boundary re-score of batch 5).

The run was at `main` bdf2c177 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran; each "after" run re-checked only the survivors, with `--only`. The tests given were each module's test files from its earlier batches, as paths relative to `sidecar/`, and they first passed once on the unmutated copy of `sidecar/`. Tests that read `docs/` or `examples/` fail in that copy, so they were left out with `--deselect`: `test_redactor_characterization.py::test_the_audit_page_shows_this_table` for `redact.py` and `test_sql_exec.py::test_service_calls_are_wired_and_audited_as_intended` for `localdata_service.py`. `test_redact.py` of batch 1 can't be collected there at all, so it wasn't given for `redact.py`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `redact.py` | test_redactor_characterization, test_secret_detectors_userinfo, test_git_secrets_shapes, test_secrets_at_rest (+ test_redact_boundary) | 2 of 2 | 0 / 2 | **0.0%** | 2 / 0 | **100%** | none |
| `localdata_service.py` | test_local_data, test_local_data_calls, test_local_delta, test_local_delta_dml, test_local_explain, test_local_transform, test_spill_leftovers, test_sql_exec (+ test_localdata_service_boundary) | 2 of 2 | 0 / 2 | **0.0%** | 2 / 0 | **100%** | none |
| `disk_full.py` | test_disk_full, test_disk_full_windows, test_out_of_memory, test_disk_full_mutation | 2 of 2 | 2 / 0 | **100%** | no change needed | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **redact.py**: a webhook URL whose path is just `/` doesn't blank every `/` in the message; a message of exactly the limit is kept whole. → `test_redact_boundary.py`.
- **localdata_service.py**: a result that has all its rows isn't marked truncated; the first rows stop being asked for once there are enough (and none are asked for when 0 are wanted). → `test_localdata_service_boundary.py`.

### Left

None.

