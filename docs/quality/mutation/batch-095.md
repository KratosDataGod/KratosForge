# Mutation testing, batch 95

Two modules that Data health and incidents depend on, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Data health from the Delta log: freshness, rows written per load against the usual, schema changes and the alerts they raise (`meta/data_health.py`).
- The incident log of an environment: opening, seeing again, recovering, escalating, manual handling, reporting, and the conditional writes behind them (`workloads/incidents.py`).

The run was at `main` 7be2906d (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. `meta/data_health.py` has 146 mutants, so all of them ran; `workloads/incidents.py` has 229, so 150 were sampled. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

The first run of `workloads/incidents.py` used test_incidents and test_incident_failures only. Each survivor was run again with test_incident_desk and test_incident_service_paths added, and the "before" figures are from that run. A mutant whose run times out counts as killed; none timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/data_health.py` | test_data_health (+ test_data_health_mutation) | 146 of 146 (140 + 6) | 119 / 27 | **81.5%** | 118/140 → 140/140 | 1/6 → 5/6 | 145 / 1 | **99.3%** | 1 equivalent |
| `workloads/incidents.py` | test_incidents, test_incident_failures, test_incident_desk, test_incident_service_paths (+ test_incidents_mutation) | 150 of 229 (146 + 4) | 117 / 33 | **78.0%** | 116/146 → 144/146 | 1/4 → 3/4 | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/data_health.py**:
  - The usual is taken from exactly the last 20 loads, and the list shows 21. Exactly 10 readable loads make a usual.
  - One row above a usual of none is high.
  - A table with no loads says so, with nothing unread.
  - A count of 0 is a count.
  - Metrics that aren't JSON, and parameters that aren't an object, are left out.
  - A naive time is read as UTC.
  - Age is rounded to two places, and exactly the promised hours is still fresh.
  - A schema alert names what was added, removed and retyped, with "column" or "columns".
  - A struct column's type is kept as JSON.
  - A table whose only load is version 0 has no earlier version, and a single load after other commits compares with the version just before it.
  - → `test_data_health_mutation.py`.
- **workloads/incidents.py**:
  - A new list is contract 1. A new incident has a 12-hex-digit id, one occurrence, and empty owner, team, criticality and evidence when the signal has none.
  - Seen again counts from 1 when the count is missing.
  - "Still happening" is noted from exactly 900 s.
  - A critical failure becomes P1 from its third occurrence.
  - A time that isn't one counts as the epoch.
  - Exactly 100 events are kept, with the first always kept, and exactly the newest 200 resolved incidents, one with no time counting as the oldest. The list sorts resolved incidents without a time last.
  - A lost race waits 0.05 s longer each time and gives up after 8 tries.
  - Escalation starts at exactly its time and says how long in minutes. It goes by the store's clock when the store has one, and by this PC's clock for an incident opened before it had one.
  - Notes, report titles and report text are cut to 2,000, 200 and 4,000 characters.
  - A resolved incident can't be resolved, acknowledged or given a new priority, and a report's priority must be one of P1 to P4.
  - → `test_incidents_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/data_health.py` | 163 | `distance > 0` to `>=` for high or low | Equivalent: it is reached only for an unusual load, whose distance is beyond the band (at least 0), so it is never 0. |
| `workloads/incidents.py` | 105 | `occurrences or 1` to `or 0` for the P1 rule | Equivalent: a missing count gives 1 or 0, both below 3, so the priority is P2 either way. |
| `workloads/incidents.py` | 116 | `len(events) > KEEP_EVENTS` to `>=` | Equivalent: at exactly the limit, the slice removed is `events[1:1]`, which is empty. |
| `workloads/incidents.py` | 201 | `indent=1` to `indent=0` for the stored document | Equivalent: only the whitespace between lines changes; every reader parses the same document. |

