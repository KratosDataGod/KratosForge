# Mutation testing, batch 255: incident_desk.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 109 ran all of its 149 mutants (75.2% before, 97.3% after).
- The incident desk: numbers, priorities, SLAs, reported incidents, who an incident reaches, and incident history in the central lakehouse (`incident_desk.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 149 of 149 mutants ran (148 point + 1 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Temporary folders were handled and checked as in batch 252, and a few killed mutants were run again by hand, each killed again.

The set is batch 109's files with test_alert_to_incident_e2e, test_incident_service_mutation, test_mesh_home, test_incident_history_mutation, test_incident_impact_mutation and test_incident_sla_mutation added. test_rpc_surface and test_central_roles were left out to keep each mutant's run short; they call the module in a line or two. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `incident_desk.py` | test_incident_desk, test_incident_desk_mutation, test_alert_to_incident_e2e, test_incident_partial_work, test_incident_service_mutation, test_mesh_home, test_incident_history_mutation, test_incident_impact_mutation, test_incident_sla_mutation (+ test_incident_desk_uncapped) | 149 of 149 (148 + 1) | 145 / 4 | **97.3%** | 144/148 → 145/148 | 1/1 → 1/1 | 146 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **incident_desk.py**: incident history keeps one order for incidents opened at the same moment, the order their latest snapshots were taken in, on every read, and a limit keeps the first of them. Batch 109 counted `unique(maintain_order=True)` to `False` as equivalent, since the rows are sorted by opening time right after; but that sort keeps the order of ties in practice (50 of 50 reads of 5,000 shuffled rows on polars 2.0.0), and without `maintain_order` the ties came out in another order on each of the 50. → `test_incident_desk_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `incident_desk.py` | 95 | the bell's rank for any other SLA state from 2 to 3 | Equivalent: every ranked state is 0 or 1, so 2 and 3 sort the same. |
| `incident_desk.py` | 237 | the history's rank for `resolved` from 2 to 3 | Equivalent: it is still the highest, so the same row wins. |
| `incident_desk.py` | 258 | the measures' history limit from 5,000 to 5,001 | Equivalent: the history itself shows at most 5,000 (`min(int(limit), 5000)`). |

