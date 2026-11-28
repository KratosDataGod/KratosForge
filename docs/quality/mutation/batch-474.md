# Mutation testing, batch 474

One module not scored before, run with `--kinds all`:
- A time limit that counts only the time the PC is awake: `AwakeTimer`, `threading.Timer`'s interface on an awake clock, and `awake_monotonic`, a deadline clock that leaves sleep out. On Windows both read QueryUnbiasedInterruptTime; elsewhere they step every second and leave out a step far longer than asked (`awake_timer.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 66 mutants, so all were run. test_awake_timer_mutation.py is older than this batch; no score for the module had been recorded.

The test set was test_awake_timer_mutation, test_awake_unbiased, test_deadlines_across_sleep, test_sql_limit_across_sleep and test_limits_across_sleep.

One mutant timed out: line 141, the deadline clock's ticker thread made with `daemon=False`. It was run again alone with a 900-second limit and timed out again. Run by hand, test_awake_timer_mutation's ticker test fails on it (`assert ... clock._ticker.daemon`), and then pytest can't exit because the ticker never ends. So it is counted as killed.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The Windows clock is tested on any OS with a stand-in kernel32 set in `ctypes.WinDLL` and `sys.platform` set for the test.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `awake_timer.py` | test_awake_timer_mutation, test_awake_unbiased, test_deadlines_across_sleep, test_sql_limit_across_sleep, test_limits_across_sleep (+ test_awake_timer_mutation_b) | 66 of 66 (58 + 8) | 52 / 14 (1 a timeout) | **78.8%** | 49/58 → 57/58 | 3/8 → 4/8 | 61 / 5 | **92.4%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **awake_timer.py**:
  - Off Windows there is no unbiased clock, even where a kernel32 could be loaded.
  - On Windows the clock reads QueryUnbiasedInterruptTime in seconds (100 ns ticks divided by 10,000,000), and a failed query raises OSError rather than giving a reading.
  - Without kernel32, or without the function in it, there is no unbiased clock.
  - A reading within a step of the last step takes no step, and neither does one exactly a step late. So a later long gap is judged from the last step taken, and is taken as sleep.
  - → `test_awake_timer_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `awake_timer.py` | 85 | `0 <= step` to `0 < step` in `awake_after` | Equivalent: a step of exactly 0 adds 0 either way. |
| `awake_timer.py` | 129 | `0 <= step` to `0 < step` in `_step_to` | Equivalent: a step of 0 then takes the other branch, which adds 0.0 for a step that isn't above 0. |
| `awake_timer.py` | 129 | `step > 0` to `step >= 0` | Equivalent: that branch is reached only for a step below 0 or past the limit, never 0. |
| `awake_timer.py` | 129 | `step > 0` to `step > 1` | Equivalent for the same reason: no step between 0 and 1 reaches that branch. |
| `awake_timer.py` | 144 | `now < self._last` to `<=` | Equivalent: when `now` equals `_last`, setting `_last` to it changes nothing, and the reading is the same. |
