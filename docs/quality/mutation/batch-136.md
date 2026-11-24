# Mutation testing, batch 136

One workload module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- What an evaluation looks for in one environment: failed runs, runs waiting for a decision, lateness, data product promises, success objectives and agents that stopped reporting, and the one line each change becomes in a Teams message (`workloads/evaluate.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/evaluate.py` | test_incidents, test_incident_desk, test_agent_clock_and_folder, test_not_set_up (+ test_evaluate_mutation) | 116 of 116 (113 + 3) | 89 / 27 | **76.7%** | 88/113 → 113/113 | 1/3 → 3/3 | 116 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/evaluate.py**:
  - A failed run with no message says "The run failed.", and one with no place or job gives empty ones. A run waiting for a decision with no message says what waits.
  - A load of exactly one row counts its rejected rows, a load with no rejected count rejected nothing, and exactly the promised share of rejected rows keeps the promise.
  - A product's lateness counts unless the caller says the entity reports it already.
  - The products a late table lets down go on its late signal, not on a failed-run signal before it.
  - An objective names the newest finished run, not one still going.
  - An agent is missing from exactly its stale time.
  - Times in messages are shown in UTC; a naive time and text that isn't a time are shown as they are.
  - A recovery says recovered or cleared, and names the environment when the incident has no name. An escalation names who, else on-call. A reopened incident says "again" and names its owner.
  - → `test_evaluate_mutation.py`.

### Left

None.

