# Mutation testing, batch 259: workloads/sla.py (every mutant)

One module, run uncapped: every mutant. Batch 138 ran all of its 70 mutants already (75.7% before, 98.6% after); this run checks the score on the module as it is now.
- SLA targets for incidents, from the policy or the defaults, and where an incident stands against them (`workloads/sla.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 70 of 70 mutants ran (65 point + 5 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. pytest was given `--basetemp` in a folder of this run's own, and that folder was checked afterwards: nothing in it was nested 40 or more folders deep. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/sla.py` | test_incident_sla_mutation, test_incident_history_mutation, test_incident_desk | 70 of 70 (65 + 5) | 69 / 1 | **98.6%** | no change needed | no change needed | 69 / 1 | **98.6%** | 1 equivalent |

No bugs were found. No new tests were needed: the one survivor is equivalent.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/sla.py` | 51 | `0 < value` to `0 <= value` | Equivalent (as in batch 138): a value of 0 is taken as "no target" just before, so it never reaches this check. |

