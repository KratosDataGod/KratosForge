# Mutation testing, batch 312: workloads/impact.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 138 ran its 36 mutants (all of them, 88.9% before, 100% after).
- Who an incident reaches: the data products built on its workload, the projects that read them, and the people to tell (`workloads/impact.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 36 of 36 mutants ran (36 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own (a small plugin, not committed), so a mutant that leaves a deep folder behind can't make the next mutants fail at startup; none did: no folder 40 or more levels deep was left, the kills show no late streak of identical times, and kills checked by hand failed on a real assertion. No `-k` filter or `--deselect` was used, and no mutant's run timed out.

The set is batch 138's files and every other test file that imports the module: test_incident_desk, test_incident_impact_mutation. The score counts only the files given.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/impact.py` | test_incident_desk, test_incident_impact_mutation | 36 of 36 (36 + 0) | 36 / 0 | **100%** | 36/36 → 36/36 | none | 36 / 0 | **100%** | none |

No bugs were found, and no new tests were needed: every mutant was already killed, so the module has no new test file.

### Left

None.

