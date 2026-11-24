# Mutation testing, batch 254: incident_service.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 109 ran all of its then 96 mutants (76.0% before, 99.0% after).
- Incidents in the app: read, evaluate and handle an environment's incidents, the agents and queue pages, and product status for the catalog (`incident_service.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 100 of 100 mutants ran (100 point, no boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Temporary folders were handled and checked as in batch 252, and a few killed mutants were run again by hand, each killed again.

The set is batch 109's files without test_dataops (about 21 s, and it calls the module in a few lines), with test_incident_service_mutation, test_alert_to_incident_e2e, test_notice_locked_outbox, test_incident_desk, test_agent_status_mutation, test_agent_takeover and test_shortcut_fixes added. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `incident_service.py` | test_incident_service_mutation, test_incident_service_paths, test_incident_partial_work, test_alert_to_incident_e2e, test_notice_locked_outbox, test_incidents, test_incident_desk, test_agent_fleet, test_agent_status_mutation, test_agent_takeover, test_product_status, test_run_as, test_shortcut_fixes, test_workload_queue (+ test_incident_service_uncapped) | 100 of 100 (100 + 0) | 98 / 2 | **98.0%** | 98/100 → 99/100 | none | 99 / 1 | **99.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **incident_service.py**: a workload's criticality is keyed by everything after `meta:<project>:` (`split(":", 2)`). Batch 109 counted `split(":", 3)` as equivalent, since no project or table name holds a colon; the test pins the key with an assignment id that has one. → `test_incident_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `incident_service.py` | 214 | `outbox is not None and post is not None` to `or` | Equivalent: both are set when `notify` is true and both are `None` when it isn't, and `_notice_post` never returns `None` (it returns `deliver.poster(...)`). |

