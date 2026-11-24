# Mutation testing, batch 253: workloads/incidents.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 95 sampled 150 of its 229 mutants (78.0% before, 98.0% after).
- Incidents: one record per problem in each environment's control area, changed only with conditional writes (`workloads/incidents.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 229 of 229 mutants ran (223 point + 6 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Temporary folders were handled and checked as in batch 252, and a few killed mutants were run again by hand, each killed again.

The set is batch 95's files and the other files that open, change or read incidents: test_incident_desk_mutation, test_incident_partial_work, test_alert_to_incident_e2e, test_agent_clock_and_folder, test_product_promises, test_workload_store_faults and test_incident_service_mutation. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/incidents.py` | test_incidents, test_incidents_mutation, test_incident_desk, test_incident_desk_mutation, test_incident_failures, test_incident_partial_work, test_alert_to_incident_e2e, test_agent_clock_and_folder, test_product_promises, test_workload_store_faults, test_incident_service_mutation, test_incident_service_paths (+ test_incidents_uncapped) | 229 of 229 (223 + 6) | 218 / 11 | **95.2%** | 214/223 → 222/223 | 4/6 → 4/6 | 226 / 3 | **98.7%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/incidents.py**:
  - Active incidents are listed newest first, then the resolved ones (`reverse=True`, and the missing opening time read as `""`).
  - Of two records for one problem, the one opened last counts, wherever it is in the document.
  - A resolve keeps its note, cut to 2,000 characters, and one without a note adds none.
  - A priority set on a record written before priorities existed says it was the policy's (`P3 → P1`), not `None`.
  - An escalation's timeline entry gives the minutes (`after_seconds / 60`); only the note's text was checked.
  - The stored document is indented one space per level. Batch 95 counted `indent=1` to `indent=0` as equivalent, since every reader parses it; it is pinned now, as the document is the one people read and restore from a backup.
  - → `test_incidents_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/incidents.py` | 105 | `occurrences or 1` to `or 0` for the P1 rule | Equivalent: a missing or zero count gives 1 or 0, both below 3, so the priority is P2 either way. |
| `workloads/incidents.py` | 116 | `len(events) > KEEP_EVENTS` to `>=` | Equivalent: at exactly the limit, the slice removed is `events[1:1]`, which is empty. |
| `workloads/incidents.py` | 197 | `len(resolved) > KEEP_RESOLVED` to `>=` | Equivalent: at exactly the limit, `resolved[:0]` drops nothing. |

