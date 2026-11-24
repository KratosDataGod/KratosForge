# Mutation testing, batch 179

One module, run uncapped: every mutant, not a sample of 150. Batch 12 sampled 150 of its then 411 mutants.
- The local pipeline runner: the checks before a run, running each list of activities, retries and policy timeouts, mocks, child pipelines, cancels and the run record (`orchestration/runner.py`).

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902. It used `--cap 100000 --seed 7 --kinds all`, so all 419 mutants ran (411 point, 8 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. As in batch 178, each pytest session got a new, empty base folder of its own. The 14 files below run in about 35 seconds together. Batch 12's other three files, `test_pipeline_script`, `test_pipeline_data` and `test_pipeline_schedules`, were left out to keep each mutant's run short. Together they take about 18 seconds, and their subjects are other modules. No test in the set reads `docs/`, `examples/` or `app/`, so no `--deselect` was needed.

Before the "after" run, the new tests were run in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests also passed 25 runs in a row, under the load of the other lanes.

A mutant whose run times out counts as killed, as in earlier batches. 25 timed out, each a wait or loop that no longer ends: the run loop of one list of activities (lines 389, 392, 411), waiting for a parallel slot (430), the retry loop and its policy timeout (447, 457, 473 to 492), the record of a step that is running (501, 507), and an activity's wait (622 to 629, 806). One more timed out in the "after" run: `left <= 0` to `< 0` in an activity's wait (line 625). With the new test's clock stopped, a wait of 0 seconds then never ends. With a real clock, a left of exactly 0 would be followed by a negative one.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/runner.py` | test_orchestration_runner_mutation, test_pipeline_runner_edges, _service_edges, _fabric_parity, test_cli_tenant_commands, test_pipeline_gotchas, _definitions, _data_edges, _notebooks_edges, _script_edges, _adapters_edges, test_orchestration_definition_edges, _runs_edges, test_pipeline_runs (+ test_orchestration_runner_uncapped) | 419 of 419 (411 + 8), uncapped | 374 / 45 (25 timeouts) | **89.3%** | 371/411 → 392/411 | 3/8 → 6/8 | 398 / 21 (26 timeouts) | **95.0%** | 18 equivalent, 3 timing only |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/runner.py**:
  - Child pipelines nest at most 8 deep: a pipeline that runs itself is refused with 7 child levels in the message.
  - An integer variable declared without a default starts at 0, for both `Int` and `Integer`.
  - A cancelled state matches no dependency condition (`False`, not just falsy).
  - A skipped step is saved as soon as it is skipped, while the run is still going, and a step is saved when it ends, not when it starts.
  - A notebook step's output is what its notebook gave.
  - Four activities run at once by default.
  - A retry waits the interval the policy gives, and 30 seconds when it gives none.
  - A container cancelled while its steps run is cancelled, not failed.
  - A list asked for after a cancel records nothing.
  - A child pipeline is waited on unless the call says not to.
  - A step that never started made no attempt.
  - An activity that isn't local is refused, even when its adapter could run.
  - Steps still waiting for a parallel slot at a cancel end cancelled, without starting.
  - Only `SecureString` parameters are hidden in the record.
  - An inactive step marked Skipped, with nothing before it, doesn't fail the run.
  - A run without notebooks says it ran on this PC.
  - A run's seconds are kept to the millisecond.
  - An Until's deadline has passed at that very moment, and a wait that ends exactly at its deadline isn't a timeout. A fake clock pins both.
  - → `test_orchestration_runner_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `orchestration/runner.py` | 326, 408, 664, 719, 768, 805 | a thread's `daemon=True` to `False` (the run's timer, each step, each ForEach item, a child's watcher, a detached child and a lakehouse read) | Equivalent in a run: each of these threads is cancelled or joined before the run ends. The flag only matters when the process exits. |
| `orchestration/runner.py` | 720, 768 | a thread's name takes 9 characters of the child's id, not 8 | Equivalent: nothing reads thread names. They only help debugging. |
| `orchestration/runner.py` | 577 | `_leaves_succeed`'s inner `return False` to `None` | Equivalent: its only caller is `all(...)`, where `None` is false too. |
| `orchestration/runner.py` | 490 | the `return "failed"` after the retry loop to `None` | Equivalent: it can't be reached. Every pass of the loop returns, or continues to a next pass, and the last pass always returns. |
| `orchestration/runner.py` | 454, 455 | the default of `policy.get("retry", 0)` 0 to 1, and of `policy.get("retryIntervalInSeconds", 30)` 30 to 31 | Equivalent: each default is used only when the key is missing, and then the `isinstance(..., int)` beside it is false, so the other branch (0, 30) is taken. |
| `orchestration/runner.py` | 450 | `getattr(adapter, "container", False)` default to `True` | Equivalent: every adapter that can run is a `LocalAdapter`, which sets `container`. |
| `orchestration/runner.py` | 249 | `getattr(entry.adapter, "can_execute", False)` default to `True` | Equivalent: the registry refuses a local entry whose adapter doesn't set `can_execute`, and every other entry is refused by its classification first. |
| `orchestration/runner.py` | 411 | `and` to `or` in the test that ends a list's run loop | Equivalent: the loop runs under the lock that steps take to set their state. Each pass starts every step whose dependencies have ended. So a pass never ends with no step running and some step still without a state. |
| `orchestration/runner.py` | 660 | `or` to `and` in ForEach's wait for a free place in its batch | Equivalent: a cancel stops the running items too, so they free their places. The check right after the wait then starts no more items. |
| `orchestration/runner.py` | 710 | a child run's depth `depth + 1` to `depth - 1` | Equivalent: the nesting limit is enforced when the top-level run is checked, and that check builds its own probes with the right depth. At run time the depth is only compared with 0, which a child's depth never is either way. |
| `orchestration/runner.py` | 730 | `child.state == "cancelled" or self.run.cancelled` to `and` | Equivalent: a child waited on is cancelled only by its watcher, and only when an enclosing Until's deadline has passed. The `ActivityFailed` that the mutant raises then becomes `cancelled` in `_attempts`, with no error or output kept. |
| `orchestration/runner.py` | 749 | the watcher's time left `deadline - now` to `deadline + now` | Timing only: the watcher then checks every 0.2 s, not at the deadline. A child is stopped at most 0.2 s later. A test would depend on the machine's speed. |
| `orchestration/runner.py` | 746, 809 | `>=` to `>` where a child's watcher and a lakehouse read compare the clock with the deadline | Timing only: they differ only when the clock reads exactly the deadline, and the next check, at most 0.2 s later, stops it either way. Both loops wait on the real clock, so a fake one can't pin them. |

