# Mutation testing, batch 314: workloads/overlap.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 143 ran its 53 mutants (all of them, 86.8% before, 98.1% after).
- Tables scheduled in two places: an agent and this PC, or two agents, running the same tables (`workloads/overlap.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 53 of 53 mutants ran (52 point + 1 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own (a small plugin, not committed), so a mutant that leaves a deep folder behind can't make the next mutants fail at startup; none did: no folder 40 or more levels deep was left, the kills show no late streak of identical times, and kills checked by hand failed on a real assertion. No `-k` filter or `--deselect` was used, and no mutant's run timed out.

The set is batch 143's files and every other test file that imports the module: test_schedule_overlap, test_schedule_overlap_mutation. The score counts only the files given.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/overlap.py` | test_schedule_overlap, test_schedule_overlap_mutation | 53 of 53 (52 + 1) | 52 / 1 | **98.1%** | 51/52 → 51/52 | 1/1 → 1/1 | 52 / 1 | **98.1%** | 1 equivalent |

No bugs were found, and no new tests were needed: every survivor is equivalent, so the module has no new test file.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/overlap.py` | 80 | `known[i + 1:]` to `known[i + 0:]` | Equivalent, as in batch 143: the extra pair is a schedule with itself, which has the same environment and place, so it is skipped. |

