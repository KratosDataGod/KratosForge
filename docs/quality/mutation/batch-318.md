# Mutation testing, batch 318: workloads/run_as.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Who runs each workload and with what credential (`workloads/run_as.py`).

Batch 147 ran all 88 of its mutants then (78.4% before, 98.9% after). It still has 88, and this run ran all 88 of 88.

The run was at `main` ea988e99e (2026-11-20), with the method of batch 315. After the run, no folder in the `--basetemp` tree was nested 40 levels deep, and the kills don't end in a streak of near-identical times. Three kills picked at random were checked by hand, and each failed real tests (3, 2 and 1). No mutant timed out.

The set is the files that name the module. No `-k` filter or `--deselect` was needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/run_as.py` | test_run_as, test_run_as_mutation | 88 of 88 (85 + 3), uncapped | 87 / 1 | **98.9%** | 84/85, no change needed | 3/3, no change needed | 87 / 1 | **98.9%** | 1 equivalent |

No bugs were found, and no new tests were needed: the one survivor is the equivalent batch 147 left.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/run_as.py` | 23 | `'expiring': 2` to `3` | Equivalent: expiring still sorts after expired and failed, the only other states listed. |

