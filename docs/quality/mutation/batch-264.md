# Mutation testing, batch 264

One module that had no score before, run with `--kinds all`:
- The SQL page's run process: reading the statements, refusing what a run may not do, the DuckDB settings it locks, each statement's result streamed into an Arrow file in batches, plans and profiles, the memory watch, and the error a failed run reports (`sql_worker.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each pytest session got its own fresh temporary folder, so a mutant that leaves a folder behind can't fail the runs after it. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because the machine was shared with two other mutation runs. The module has 181 mutants, so 150 were sampled (148 point and 2 boundary). `test_sql_exec.py::test_service_calls_are_wired_and_audited_as_intended` reads `app/src-tauri/src/watchdog.rs`, so it was left out with `--deselect`. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_worker.py` | test_sql_worker_cov2, test_sql_worker_edges, test_sql_worker_inprocess, test_sql_exec (one test deselected) (+ test_sql_worker_mutation) | 150 of 181 (148 + 2) | 125 / 25 | **83.3%** | 124/148 → 143/148 | 1/2 → 1/2 | 144 / 6 | **96.0%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **sql_worker.py**:
  - A result is written in batches of at most 65,536 rows. DuckDB 1.5 fills each batch, while 2.0 may give smaller ones.
  - The spill folder is made with its parents, and one already there is used.
  - A plan lists 200 steps and counts the rest.
  - A warehouse run that stops keeps its own kind ("throttled", "timeout" and so on) and names the batch it stopped at. The same stop in another run, or in a split script, is a plain SQL error with no batch.
  - Running out of memory says to narrow the query or raise the share.
  - A failure before any statement starts names no statement. A failed statement of a split script is named, and a long message is cut to 4,000 characters.
  - The lakehouse keeps its workspace name.
  - Each statement of a split script gets its own parameter values, also when it isn't the unit's last statement. Each part's seconds are rounded to hundredths.
  - Progress is said no more often than its interval.
  - → `test_sql_worker_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sql_worker.py` | 44 | `int(fields.get("statement") or 0)` to `or 1` | Equivalent: `statement` events always carry the statement's number, 1 or more. |
| `sql_worker.py` | 126 | the first `rows` 0 to 1 | Equivalent: a split script has at least one statement, and each one sets `rows` before it is read. |
| `sql_worker.py` | 202 | `>= PROGRESS_SECONDS` to `>` | Equivalent: the time since the last progress event is never exactly the interval. |
| `sql_worker.py` | 265 | `job.get('free_mb') or 0` to `or 1` | Equivalent: 0 MB and 1 MB both read "0.0 GB". |
| `sql_worker.py` | 305 | the sign-in wait 600 s to 601 s | Equivalent: only how long a sign-in may take changes. |
| `sql_worker.py` | 399 | the listening thread's `daemon=True` to `False` | Equivalent: the process ends with `os._exit`, which doesn't wait for threads. |
