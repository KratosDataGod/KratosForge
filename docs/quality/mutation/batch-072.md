# Mutation testing, batch 72

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The run logs on this PC that grow one line at a time and trim themselves (`runlog.py`).
- Copy jobs from a Fabric workspace: the list, the definition and the runs (`workspace_copy_jobs_service.py`).
- Who can read what: the workspace access review, OneLake data access roles and who can read a table (`access.py`).

The run was at `main` 4c0ab9a0e (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `timeutil.py` and `workspace_pipelines_service.py` were also run and already scored 100% (16/16 and 12/12), so they are not in the table.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `runlog.py` | test_local_retention_gaps, test_local_schedules_mutation (+ test_runlog_mutation) | 23 of 23 (21 + 2) | 7 / 16 | **30.4%** | 7/21 → 21/21 | 0/2 → 2/2 | 23 / 0 | **100%** | none |
| `workspace_copy_jobs_service.py` | test_workspace_copy_jobs (+ test_workspace_copy_jobs_mutation) | 9 of 9 (9 + 0) | 8 / 1 | **88.9%** | 8/9 → 9/9 | none | 9 / 0 | **100%** | none |
| `access.py` | test_access_cache, test_access_cache_service_edges, test_access_cache_service_mutation, test_estate, test_estate_service_edges, test_fail_closed, test_shortcut_center, test_shortcut_fixes (+ test_access_mutation) | 84 of 84 (84 + 0) | 75 / 9 | **89.3%** | 75/84 → 84/84 | none | 84 / 0 | **100%** | none |

One bug was found, in `access.py`, and filed as #2819: a data access role name ending in a line break passes the name check, though a role name is letters and digits only. A strict xfail pins it: `test_a_role_name_ending_in_a_line_break_is_refused`. Whoever fixes #2819 drops that xfail in the same PR.

### What the survivors were, and the tests that now kill them

- **runlog.py**:
  - The default sizes are 8 MB and 10,000 rows.
  - A log in folders that don't exist yet is created with them.
  - A file exactly at the trim size is left alone; one row over it keeps half of it by default, the newest rows.
  - Rows that add up to exactly the size kept are all kept, and one byte less keeps one row fewer.
  - The row limit wins when it is smaller, and the newest row is kept even when it alone is over the size kept.
  - → `test_runlog_mutation.py`.
- **workspace_copy_jobs_service.py**:
  - The newest 50 runs are listed: 49 and 50 are listed whole, and 51 or more are cut to the first 50.
  - → `test_workspace_copy_jobs_mutation.py`.
- **access.py**:
  - A person is named by display name, then email, then id, and a guest account is recognised in any letter case.
  - Which review notes each person gets, in Prod and Dev, and that people with notes come first, then users, then by name.
  - A role's paths come only from its Permit rules about paths.
  - How a read role is built, which names, paths and member lists are refused, and that an edited role keeps its item members.
  - Which role paths reach a table: a path that only starts the same way doesn't.
  - Which answers mean OneLake security is off, and who can read a table and why.
  - → `test_access_mutation.py`.

### Left

None.

