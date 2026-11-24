# Mutation testing, batch 315: workloads/health.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). It has no `<` or `>` comparison, so it has no boundary mutants.
- A workload's health, readiness and findings, each with its evidence (`workloads/health.py`).

Batch 137 ran all 91 of its mutants then (92.3% before, 100% after). It still has 91, and this run ran all 91 of 91.

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902 and the method of batch 183: tests first passed on the unmutated copy, paths were relative to `sidecar/`, and each pytest session got a fresh `--basetemp` folder of its own from a small run-only plugin that isn't committed. After the run, no folder in that tree was nested 40 levels deep, and the kills don't end in a streak of near-identical times. Three kills picked at random were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (20, 1 and 1). No mutant timed out.

The set is the files that name the module. No `-k` filter or `--deselect` was needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/health.py` | test_workload_health, test_workload_health_mutation, test_workloads | 91 of 91 (91 + 0), uncapped | 91 / 0 | **100%** | no change needed | none | 91 / 0 | **100%** | none |

No bugs were found, and no new tests were needed.

### Left

None.

