# Mutation testing, batch 229

One module, run uncapped: every mutant, not a sample. Batch 22 sampled 150 of its then 248 point mutants (84.7% before, 97.3% after), and batch 166 ran its 5 boundary mutants (60.0% before, 80.0% after).
- Schedules that run on this PC through Windows Task Scheduler (`local_schedules.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 253 of 253 mutants ran (248 point + 5 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is batch 22's files with test_local_schedules_boundary (batch 166) and test_schedule_service_uncapped (batch 174), about 26 s a session. Other files that use the module (the agent, notebook batch, workload and uninstall tests) were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `local_schedules.py` | test_local_schedules_mutation, test_local_schedules_boundary, test_local_schedules_edges, test_schedules, test_schedule_config, test_schedule_edges, test_schedule_locks_and_lists, test_schedule_service_mutation, test_schedule_service_uncapped, test_signed_out_schedules, test_pipeline_schedules, test_schedule_clock_changes, test_shared_schedules, test_schedule_edits, test_json_files, test_notebook_folders, test_notebook_runs (+ test_local_schedules_uncapped) | 253 of 253 (248 + 5) | 237 / 16 | **93.7%** | 233/248 → 240/248 | 4/5 → 4/5 | 244 / 9 | **96.4%** | 9 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **local_schedules.py**:
  - `pythonw` picks pythonw.exe only when it is next to the interpreter.
  - Task Scheduler's last results are asked for only where it runs.
  - The runs list gives the newest 500 by default.
  - A new schedule's id is 12 hex characters.
  - A schedule saved before it had an `enabled` field counts as on: its task is saved enabled, the list shows its next run, and a signed-out one is paused when the policy forbids it.
  - → `test_local_schedules_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `local_schedules.py` | 86 | `replace(microsecond=0)` → `microsecond=1` | Equivalent, as in batch 22: the start time is written with `%S`, which drops microseconds. |
| `local_schedules.py` | 107 (2) | `now` rounded to `second=1` or `microsecond=1` instead of 0 | Equivalent: every time it is compared with (a schedule time, a start, a grid time) is a whole minute, so a minute and a minute plus a second order the same way against them. |
| `local_schedules.py` | 119 | `nxt - now > step` to `>=` | Equivalent, as in batch 166: at exactly one step, `now` is on the grid, and the grid search picks the same next time. |
| `local_schedules.py` | 121 | `nxt + k * step` → `nxt - k * step` | Equivalent, as in batch 22: `k` runs from `-span` to `span`, so the same grid times are made in another order. |
| `local_schedules.py` | 121 | `range(-span, span + 1)` → `range(-span, span)` | Equivalent: `nxt` itself (k = 0) at one of its two folds is still to come, and the last grid time is more than an hour after it, so it is never the earliest. |
| `local_schedules.py` | 125 | `range(7)` → `range(8)` for a daily schedule | Equivalent, as in batch 22: `weekday()` is never 7. |
| `local_schedules.py` | 126 | `not times or not days` → `and` | Equivalent, as in batch 22: with no times or no days, the loop after it finds nothing and returns `None` anyway. |
| `local_schedules.py` | 128 | `range(8)` → `range(9)` days ahead | Equivalent: a schedule's weekday comes round again within offsets 0 to 7, so offset 8 is never reached. |
