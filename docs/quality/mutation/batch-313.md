# Mutation testing, batch 313: workloads/incident_history.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 139 ran its 86 mutants (all of them, 79.1% before, 100% after).
- Incident history in the central lakehouse (event and snapshot rows, the record ids already copied) and the report of time to acknowledge, time to resolve and SLAs met (`workloads/incident_history.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 86 of 86 mutants ran (83 point + 3 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own (a small plugin, not committed), so a mutant that leaves a deep folder behind can't make the next mutants fail at startup; none did: no folder 40 or more levels deep was left, the kills show no late streak of identical times, and kills checked by hand failed on a real assertion. No `-k` filter or `--deselect` was used, and no mutant's run timed out.

The set is batch 139's files and every other test file that imports the module: test_corrupt_state_reads, test_incident_desk, test_incident_desk_mutation, test_incident_history_mutation. The score counts only the files given.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/incident_history.py` | test_corrupt_state_reads, test_incident_desk, test_incident_desk_mutation, test_incident_history_mutation | 86 of 86 (83 + 3) | 86 / 0 | **100%** | 83/83 → 83/83 | 3/3 → 3/3 | 86 / 0 | **100%** | none |

No bugs were found, and no new tests were needed: every mutant was already killed, so the module has no new test file.

### Left

None.

