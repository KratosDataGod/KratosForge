# Mutation testing, batch 311: workloads/queue.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 146 ran its 105 mutants (all of them, 77.1% before, 98.1% after).
- One list of running, queued and next work across this PC, each agent and Fabric (`workloads/queue.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 105 of 105 mutants ran (101 point + 4 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own (a small plugin, not committed), so a mutant that leaves a deep folder behind can't make the next mutants fail at startup; none did: no folder 40 or more levels deep was left, the kills show no late streak of identical times, and kills checked by hand failed on a real assertion. No `-k` filter or `--deselect` was used, and no mutant's run timed out.

The set is batch 146's files and every other test file that imports the module: test_workload_queue, test_workload_queue_mutation. The score counts only the files given.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/queue.py` | test_workload_queue, test_workload_queue_mutation | 105 of 105 (101 + 4) | 103 / 2 | **98.1%** | 99/101 → 99/101 | 4/4 → 4/4 | 103 / 2 | **98.1%** | 2 equivalent |

No bugs were found, and no new tests were needed: every survivor is equivalent, so the module has no new test file.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/queue.py` | 153 | `'next': 4` to `5` | Equivalent, as in batch 146: only the order of the ranks is used, and next still sorts after every other state. |
| `workloads/queue.py` | 163 | the default order `9` to `10` | Equivalent, as in batch 146: every row has one of the five states in `_ORDER`, so the default is never used. |

