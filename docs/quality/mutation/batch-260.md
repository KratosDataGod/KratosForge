# Mutation testing, batch 260: workloads/evaluate.py (every mutant)

One module, run uncapped: every mutant. Batch 136 ran all of its 116 mutants already (76.7% before, 100% after); this run checks the score on the module as it is now.
- What an evaluation looks for in one environment: failed runs, runs waiting for a decision, lateness, data product promises, success objectives and agents that stopped reporting, and the one line each change becomes in a Teams message (`workloads/evaluate.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 116 of 116 mutants ran (113 point + 3 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. pytest was given `--basetemp` in a folder of this run's own, and that folder was checked afterwards: nothing in it was nested 40 or more folders deep. Since every mutant was killed, seven of them (indexes 60, 90, 105, 110, 113, 114 and 115, among the last run) were run again with a new temporary folder for each pytest session: all seven were killed again. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/evaluate.py` | test_evaluate_mutation, test_incidents, test_incident_desk, test_agent_clock_and_folder, test_not_set_up, test_incident_desk_mutation, test_incident_service_mutation, test_product_promises | 116 of 116 (113 + 3) | 116 / 0 | **100%** | no change needed | no change needed | 116 / 0 | **100%** | none |

No bugs were found. No new tests were needed: every mutant was killed.

### Left

None.

