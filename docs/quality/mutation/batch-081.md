# Mutation testing, batch 81

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The optimization assessment of a workspace or a capacity, its read budget, and sharing it (`opportunities_service.py`).
- Databricks job runs and Lakeflow pipeline updates for Monitor (`engines/databricks_jobs.py`).
- Which engine each notebook runs on, switching it, and closing its sessions (`engines/sessions.py`).

The run was at `main` fccfe5bb2 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_rpc_surface.py` also calls `optimization_assess`, but it was left out because two of its tests read the app's own files under `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `opportunities_service.py` | test_opportunities, test_opportunities_share (+ test_opportunities_service_mutation) | 81 of 81 (77 + 4) | 53 / 28 | **65.4%** | 51/77 → 77/77 | 2/4 → 4/4 | 81 / 0 | **100%** | none |
| `engines/databricks_jobs.py` | test_databricks_jobs, test_databricks_jobs_edges, test_databricks_runs_service (+ test_databricks_jobs_mutation) | 133 of 133 (131 + 2) | 106 / 27 | **79.7%** | 106/131 → 130/131 | 0/2 → 1/2 | 131 / 2 | **98.5%** | 2 equivalent |
| `engines/sessions.py` | test_engines, test_engines_service, test_engine_switch_failures, test_kudp_guardrails (+ test_engine_sessions_mutation) | 64 of 64 (64 + 0) | 62 / 2 | **96.9%** | 62/64 → 64/64 | none | 64 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **opportunities_service.py**:
  - A capacity reads at most 25 workspaces, in name order ignoring case, and says how many were left out (none for one workspace).
  - One workspace keeps the id, name and capacity Fabric gave, or the id asked for when Fabric gives none.
  - Days are kept between 1 and 90, and default to 30; the demonstration also uses 30.
  - Past 300 items the rest are listed as not read; a small workspace has no problem.
  - Reads run on four workers.
  - The read budget allows exactly its calls, stops at its deadline even with calls left, and reports 1,500 calls, not reached, when unspent.
  - A workspace, its definitions or its histories past the budget, or that can't be read, are reported as not read, never raised.
  - An assessment is kept under its run id; five are kept, and a sixth drops the oldest.
  - Sharing rebuilds the rows from the kept assessment and passes approve and confirm through; one that isn't kept says so and shares nothing.
  - → `test_opportunities_service_mutation.py`.
- **engines/databricks_jobs.py**:
  - At most 100 job runs and 50 pipelines are read, with 5 updates each.
  - A task's error is cut to 1,000 characters, and a run page or notebook path to 500.
  - A state with no words of its own is title-cased, or reads as Unknown.
  - A missing or negative time is 0, and a run without a start or an end took nothing.
  - Compute is Serverless with an environment or no task list; a run whose tasks name no compute uses its own.
  - A failed run without a failed task says only "Failed".
  - A pipeline update took 0 s and says its trigger, and one made exactly at the window's start is kept.
  - Fewer than one day reads one day, and a failed sign-in marks both lists not ok.
  - → `test_databricks_jobs_mutation.py`.
- **engines/sessions.py**:
  - A session that can't be closed, on an engine no longer registered, gets the short local note.
  - The logged close failure is cut to 300 characters.
  - → `test_engine_sessions_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/databricks_jobs.py` | 172 | default `days=1` to `0` | Equivalent: `list` keeps days between 1 and 30, so a default of 0 reads one day too. |
| `engines/databricks_jobs.py` | 77 | `end >= start` to `end > start` | Equivalent: when the end equals the start, both give 0 seconds. |

