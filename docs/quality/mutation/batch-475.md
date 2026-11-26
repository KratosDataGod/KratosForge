# Mutation testing, batch 475

The notebook debugger, scored for the first time, run with `--kinds all`:
- Breakpoints and stepping in a cell: the trace, pauses, Step into / over / out, Evaluate, Stop (`debugger.py`, the whole module).
- The debugger's parts of the kernel (`notebook.py`, only the lines listed below).
- The app side of the debugger (`app/ui/debugging.js`), by hand, since there is no JS mutation tool.

The run was at `main` c9716c4 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (seed 7, `--kinds all`), on Python 3.12.3 and DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because the test set takes about 100 seconds with four workers; no mutant's run timed out. Each test set was run with `-x` and `-n 4` (`-n 3` for notebook.py, which ran beside it), passed as extra pytest arguments after `--`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. One test reads `examples/`, outside `sidecar/`, and was left out with `--deselect tests/test_notebook_parity.py::test_spark_reads_files_and_groups_like_pyspark`. Runs that reached the two-hour limit of the background job were carried on with `--only` and the indexes not yet run, with the same `--cap` and `--seed`, so every index was run once.

### What was run

- **debugger.py**: `--cap 300 --seed 7`. The module has 224 mutants, so all were run.
- **notebook.py**: `--cap 3000 --seed 7` (the module has 2009 mutants, so the plan is all of them), with `--only` set to the 113 mutants whose line is in these ranges, found from `mutate.points` by line number:
  - `_Run.set_paused` (404 to 407), `finish` clearing the pause (566) and `snapshot` reporting it (587, 588);
  - `_breakpoint_hook` and its install (719 to 739);
  - `Kernel.forget_guest` and `rename_guest` (815 to 835);
  - the debugger branches of `start` (1097 to 1100), `interrupt` (1140, 1141), `debug` (1162 to 1167), `_execute` (1206 to 1214) and `_exec_python` (1372 to 1378);
  - `_describe`, `_secret_preview`, `_debug_redacted`, `_secret_values` and `_frame_row` (1851 to 1904);
  - the secret and slow paths of `variable_detail` (1913 to 1916, 1928, 1932);
  - `_bounded` (2147 to 2179).

  The first run was started with slightly wrong ranges; 16 of its mutants were outside these functions and aren't counted, and the 11 it missed were run on their own.
- **Tests**: test_debugger, test_debugger_concurrency, test_debugger_engine, test_debugger_eval, test_debugger_hardening, test_debugger_language, test_debugger_midstep, test_debugger_robust, test_debugger_saved, test_debugger_secrets, test_debugger_soak, test_variables_secrets, test_notebook_cov3 and test_notebook_parity (the two notebook files with debugger and Variables pane tests, about 5 seconds), for both modules. The "after" runs add test_debugger_mutation.
- **debugging.js**: 13 hand mutants of its main guards, each applied to the file, run against `app/tests/browser/debugger.test.cjs` (`node --test --test-concurrency=1`, the real engine from the venv), and the file put back. The survivors were run again against that file and `app/tests/ui.test.cjs`. The mutants:
  - `dbgSpanGone`: `end > start` to `>=`, and `a <= start` to `<`;
  - the breakpoint limit: `dbgBpLimit` 100 to 101, and `set.size >= dbgBpLimit` to `>`;
  - `dbgSnapshot` and `dbgSent`: not cleared when the run ends, the gap never noted, and `"sent" || (sent && gap)` to `&&`;
  - saying a pause once: every snapshot of a pause counted as new, and a new pause never said;
  - `dbgCommand`: no one-command-at-a-time guard, and Evaluate's answer treated as a step's;
  - `dbgPausedIn`: `some` to `every`, and always false.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `debugger.py` | the 14 above (+ test_debugger_mutation) | 224 of 224 (212 + 12) | 181 / 43 | **80.8%** | 174/212 → 205/212 | 7/12 → 10/12 | 215 / 9 | **96.0%** | 9 equivalent |
| `notebook.py` (debugger parts) | the 14 above (+ test_debugger_mutation) | 113 of 2009 (112 + 1) | 96 / 17 | **85.0%** | 96/112 → 108/112 | 0/1 → 1/1 | 109 / 4 | **96.5%** | 4 equivalent |
| `app/ui/debugging.js` (by hand) | debugger.test.cjs (+ ui.test.cjs, and one new test in debugger.test.cjs) | 13 | 10 / 3 | **76.9%** | | | 12 / 1 | **92.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **debugger.py**:
  - The limits are the documented ones: 100 breakpoints, 200 variables, 5 skipped names, 20 evaluations, 2000 characters, 10 and 3 seconds. Every test read them by name, so a changed number went unseen; the app states two of them to the person.
  - 100 breakpoints are taken and 101 refused; an expression of exactly 2000 characters is evaluated and one more is refused.
  - Resuming answers `{"resumed": action}`.
  - A command for a cell that isn't paused is refused, and nothing is queued for a later pause.
  - `stop()` says whether the cell was paused, queues a stop only then, and always marks the debugger as stopping. A pause reached after Stop was asked for (Cancel while the cell ran, and the cell caught the interrupt) ends the cell at once instead of waiting for a command.
  - Once Stop is asked for, an interrupt landing in an expression ends the cell; it isn't shown as the expression's answer.
  - `pause_here` (`breakpoint()`) is True once it has paused in the cell's code, and False for other code.
  - Variables past the describe time are listed unread, never expandable, and the time is up at the deadline itself.
  - A class body that has returned isn't kept by the debugger; a function a step started in lets go of its variables when it returns.
  - Step over a one-line loop that calls the cell's own function runs the whole loop.
  - A step that lands on a breakpoint line pauses as a step; Continue to one pauses as a breakpoint.
  - A skipped call whose module name isn't text (or is empty) is named by its function alone.
  - The trace's yes/no helpers (`_stops`, `_stops_on_return`, `_suspends`) answer False, not None. Their callers only test them for truth, so these four mutants are pinned by the helpers' own answers.
  - → `test_debugger_mutation.py`.
- **notebook.py**:
  - `breakpoint()` on a thread that runs no cell goes to the hook that was there before, with its arguments, and returns its answer.
  - Forgetting a guest in a kernel that ran nothing works; forgetting another guest leaves the paused cell paused, and forgetting its own cancels it.
  - Renaming a guest says it renamed something when only its variables moved, and when only its last cell did.
  - Cancel while paused answers `{"interrupted": True, "stopped": True}`.
  - `_bounded` with no time left gives the fallback without reading and without a timer. An opened variable past its time lists its items as unread.
  - A read that ends as its timer fires takes back the interrupt it sent; a quick read sends nothing; a timer that fires after the read ended sends nothing. These three use a stand-in for `ctypes.pythonapi` and for the timer, so nothing is raised in the test's thread.
  - → `test_debugger_mutation.py`.
- **debugging.js**:
  - `dbgSpanGone` with an empty span (`end > start` to `>=`) survived the browser test, and is killed by the existing `ui.test.cjs` test "a line counts as deleted only when one change takes all of it and its line break".
  - A snapshot with no pause while a step is on its way (`dbgGap`): when the next pause comes before the step's answer, it is said once, and the answer doesn't make the next poll of the same pause say it again. → a new test in `debugger.test.cjs`, which drives `dbgSnapshot` the way the engine's answers would arrive.

Each "after" figure is a re-run with `--only` of every survivor (for JS, each survivor again), with the new tests in the set. Adding a test can't revive a killed mutant.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `debugger.py` | 150, 161 | `return self._line` dropped to `None` in the line tracer | Equivalent on CPython: a local trace function that returns `None` for a line, return or exception event keeps the frame's tracer (only a `call` event's `None` turns tracing off for the frame). Checked on 3.12.3. |
| `debugger.py` | 78 | `self.depth = 0` to `1` | Equivalent: `depth` is read only by Step over, Step out and the return check, and is set from `_depth` on every resume before any of them runs. Before the first pause the mode is Continue (no depth test) or Step (stops at the first line). |
| `debugger.py` | 175 | `n = 0` to `1` in `_depth` | Equivalent: every depth is one more, and depths are only compared with each other (the saved `depth` comes from `_depth` too). |
| `debugger.py` | 98 | `add(..., tail=False)` default to `True` | Equivalent: `_exec_python` is the only caller. When the cell has a last expression, the second `add(tail, tail=True)` sets `_tail` again; when it has none, `_tail` is the body, whose `_top_line` is then never used. |
| `debugger.py` | 123 | `event == "call" and not ... CO_OPTIMIZED` to `or` | Equivalent: the global trace function only gets `call` events, so the test is true for every frame, and a function's own namespace is never read from `_scopes` (`_scope` returns `f_locals` for optimized code first). Its entry goes at its return. |
| `debugger.py` | 337 | `0 <= f_lasti` to `1 <= f_lasti`, and to `0 < f_lasti` | Equivalent: offset 0 of a generator's or coroutine's code is `RETURN_GENERATOR` (3.11 and later), never where a return event fires, so the answer is the same. |
| `debugger.py` | 337 | `f_lasti < len(code)` to `<=` | Equivalent: a frame's last instruction is always inside its code; the bound only guards the index. |
| `notebook.py` | 723 | `_routers()[0]` to `[1]` in `_breakpoint_hook` | Equivalent: stdout's and stderr's routers always get the same target for a thread (`_execute`, `_capture`, the Output widget), so either finds the same run. |
| `notebook.py` | 728 | `if not pause_here(frame): return None` negated | Equivalent: both branches return `None`. |
| `notebook.py` | 1861 | `("", False)` to `("", True)` for a hidden value in `_describe` | Equivalent: the flag is only used as `hidden or holds_secret`, and `hidden` is already true there. |
| `notebook.py` | 2166 | `timer.daemon = True` to `False` in `_bounded` | Equivalent: the timer is always cancelled in the `finally` before `_bounded` returns, so its thread ends at once and never holds up the process's exit. |
| `debugging.js` | 540 | `if (snap?.status !== "running") cell.dbgSent = null` removed | Equivalent: `dbgStartArgs` sets `dbgSent` to null before every run, and a cell that isn't running is never paused, so `dbgCommand` never reads the stale value. |
