# Mutation testing, batch 219

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Runs that external tools start: output limits, records on disk, the wait, stops and cancels, the SQL slot and the cells of a notebook run (`integrations/executions.py`).

Batch 23 sampled 150 of this module's 157 mutants then (77.3% before, 98.0% after), and batch 165 re-scored its 6 boundary mutants (16.7% before, 66.7% after). It now has 163, and this run ran all 163 of 163.

The run was at `main` ddc89182d (2026-11-20), with the method of batch 218: tests first passed on the unmutated copy, paths were relative to `sidecar/`, and each pytest session got a fresh `--basetemp` folder of its own from a small run-only plugin that isn't committed and that removed the folder when the session ended. A fixed per-lane `--basetemp` isn't enough, because pytest deletes it at the start of every session, so a folder nested too deep by one mutant would fail every later session and score each later mutant as killed. No folder was nested 40 levels deep. The last kills of the run all took about 50 seconds, so five of them were checked by hand on top of three picked at random: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (the three random ones 1, 2 and 16; the late ones 1 and 1). They take that long because the tests that kill them are in the files after `test_integrations_executions.py`, which runs for about 42 seconds.

The set is every test file that imports the module. It runs in about 55 seconds on this busy machine, nearly all of it `test_integrations_executions.py`, whose stop and cancel tests are the ones this module most needs, so no file was left out. No `-k` filter or `--deselect` was needed.

One mutant timed out at the run's 120 s limit, and this page first counted it as killed, saying the stop waited for ever for a kernel still running. That was wrong. Re-run alone at the batch's own commit with a 1500 s limit (2026-11-24), the set passed in 166 s: with `and` swapped for `or` in a notebook run's stop (line 383), the stop sleeps out the whole grace period before it looks at the kernel, which the tests' kernels have already left running, so it is only slower. It was a survivor, not a kill, and the numbers below are corrected. A test was then added in `tests/test_integrations_executions_mutation2.py`: a stop sleeps only while the kernel is running (one 0.1 s sleep for a kernel that reports running once), with this module's clock replaced so another thread's sleep isn't counted. With it, mutant 98 was re-run: `--only 98` timed out again at 120 s, because the set itself takes about 190 s on this loaded machine, so it was applied by hand to a copy of `sidecar/` and the set run without a limit: the new test failed (2,556,184 sleeps instead of 1) and the other 126 passed. It counts as killed by that hand run.

The run otherwise scored over 95%, and every other survivor is equivalent or a matter of timing only.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/executions.py` | test_attach_race, test_integrations_app, test_integrations_executions, test_integrations_executions_boundary, test_integrations_executions_edges, test_integrations_executions_mutation, test_integrations_registry_edges; after: + test_integrations_executions_mutation2 | 163 of 163 (157 + 6), uncapped | 157 / 6 | **96.3%** | 153 / 157 (97.5%) → 154 / 157 (98.1%) | 4/6, no change needed | 158 / 5 | **96.9%** | 2 equivalent, 3 timing only |

No bugs were found.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `integrations/executions.py` | 93 | after the output cut, the budget left set to `1`, not `0` | Equivalent: every later item's JSON is longer than one character, so it is cut just the same. |
| `integrations/executions.py` | 329 | `have_slot = False` to `True` before the SQL slot is waited for | Equivalent: the first thing the `try` does is set `have_slot` from the slot's `acquire`, so the first value is never read. |
| `integrations/executions.py` | 280 | `time.monotonic() >= deadline` to `>` while a run is waited for | Timing only, as batch 165 found: at exactly the deadline the run is returned one 0.05 s poll later. |
| `integrations/executions.py` | 306 | a cancel waits 3 × the grace period, not 2 | Timing only, as batch 23 found: it changes only how long a cancel waits for work that hasn't stopped, and a test of it would be slow and depend on timing. |
| `integrations/executions.py` | 383 | `time.monotonic() < deadline` to `<=` while a cancelled run's kernel stops | Timing only, as batch 165 found: at exactly the deadline the kernel is ended one 0.1 s poll later. |
