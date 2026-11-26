# Mutation testing, batch 174

One module, run uncapped: every mutant, not a sample of 150. Batch 12 sampled 150 of its then 369 mutants (55.3% before with its first set of files, 100% after).
- Schedules on this PC and Fabric's schedules in one list: run locks and their takeover, a run's row, a Prod run's audit lines (#3068), a pipeline run followed to its end, Teams alerts that don't go out, and the scan of Fabric's schedules (`schedule_service.py`).

The run was at `main` bdf2c1775 (2026-11-19), after #3068 changed the module, with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 386 of 386 mutants ran (382 point + 4 boundary). A first run on the code before #3068 was set aside, and its figures aren't used. Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. pytest was given `--basetemp` in a folder of this run's own, as in batch 172, and that folder was checked afterwards: no mutant left a deep folder behind that could have failed the next mutants at startup. In the "after" run each pytest session got a new temporary folder of its own (a small plugin, not committed).

The set is batch 12's files, with test_failclosed_paths (the Prod audit lines) added, and without the slower ones that call the module least: test_notebook_batch_stop (about 4.5 s), and test_workloads, test_redact, test_silent_except and test_meta_engine_more_edges, which each touch the module in a few lines. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used. No mutant's run timed out.

Since the run, #3091 changed `schedule_service.py` (Prod PC schedules decide fail-closed before taking the overlap lock), so the score is for the module at bdf2c1775. The new tests pass unchanged on `main` 82f8e2101.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `schedule_service.py` | test_schedule_service_mutation, test_schedule_service_edges, test_schedule_locks_and_lists, test_signed_out_schedules, test_schedules, test_failclosed_paths, test_notebook_runs, test_pipeline_schedules, test_notebook_batch, test_notebook_batch_edges, test_cli_local_commands, test_fabric_pipeline_runs, test_fabric_connections, test_integrations_scope_edges, test_integrations_workspace_scope, test_risk (+ test_schedule_service_uncapped) | 386 of 386 (382 + 4) | 350 / 36 | **90.7%** | 350/382 → 374/382 | 0/4 → 4/4 | 378 / 8 | **97.9%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **schedule_service.py**:
  - `pc_schedules` itself was never run: every test replaces it. It now is, with this PC's folder and IT's signed-out setting.
  - A lock's identity that is empty or isn't text isn't checked.
  - A Linux process identity is field 22 of `/proc/<pid>/stat`, read after the last `)`.
  - On Windows a pid is opened for limited query only (0x1000), not inherited, and pid 0 or below counts as running.
  - Run now passes the no-window flag only where there is one.
  - A notebook's runs come from this PC's last 500 runs; the full list shows the last 200.
  - Starting the app counts the runs that stopped.
  - A lock makes its folder and the folders above it.
  - A takeover guard exactly a minute old is still respected, and one just over is cleared. A lock exactly 12 hours old of a running process is kept.
  - A stopped run's start is kept up to 40 characters.
  - A run skipped behind a running one took 0 seconds; a run's length is its monotonic seconds to a tenth.
  - A run whose lock was taken over meanwhile is still logged, and waiting alerts still go when their lock was.
  - A scheduled pipeline is followed without values, then read whole once.
  - A Prod run the audit log refused took 0 seconds and gives its lock back; a failed Prod run's audit error is cut at 300 characters.
  - An alert Teams refused is audited as one queued; one sent with no answer as one unconfirmed.
  - A Fabric scan keeps each schedule's configuration and names the first failed read; a workspace list that fails is no longer pending.
  - → `test_schedule_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `schedule_service.py` | 240 | `finished_at and started_at` to `or` | Equivalent: `seconds_between` gives `None` when either time is missing, which is what the line gives. |
| `schedule_service.py` | 316, 330, 332 | `unlink(missing_ok=True)` to `False` for a stale guard, a lock taken over, and this call's own guard (3 mutants) | Equivalent: each file was stat'ed, read or made by this same call just before; it is gone only if another process removes it in between. |
| `schedule_service.py` | 404 | the refused Prod run's `lock.unlink(missing_ok=True)` to `False` | Equivalent: `_lock` made that file a few lines before, in this same call. |
| `schedule_service.py` | 363 | `_log_stopped_run`'s `return False` to `None` | Equivalent: its one caller only tests whether the answer is true. |
| `schedule_service.py` | 603 | the Fabric scan's thread `daemon=True` to `False` | Equivalent: the scan always ends; the flag only matters while Python exits. |
| `schedule_service.py` | 653 | the scan's pool from 8 to 9 workers | Equivalent: the same reads run, and the results come back in order either way. |

