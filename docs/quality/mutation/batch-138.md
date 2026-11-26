# Mutation testing, batch 138

Two small incident modules not covered before, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Who an incident reaches: the data products built on its workload, the projects linked to them and the people to tell (`workloads/impact.py`).
- SLA targets for incidents, from the policy or the defaults, and where an incident stands against them (`workloads/sla.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before each "after" run, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/impact.py` | test_incident_desk (+ test_incident_impact_mutation) | 36 of 36 (36 + 0) | 32 / 4 | **88.9%** | 32/36 → 36/36 | none | 36 / 0 | **100%** | none |
| `workloads/sla.py` | test_incident_desk (+ test_incident_sla_mutation) | 70 of 70 (65 + 5) | 53 / 17 | **75.7%** | 53/65 → 65/65 | 0/5 → 4/5 | 69 / 1 | **98.6%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/impact.py**:
  - A product without a name is named by its id, and one without an owner, domain or status gets empty ones; a link without an alias gets an empty one. A product and link that have them keep them.
  - → `test_incident_impact_mutation.py`.
- **workloads/sla.py**:
  - The default targets for P1 to P4, and the longest target, a year in minutes.
  - A target from 1 minute up to a year is taken; -1 and a minute over a year are ignored and reported.
  - An incident with no opening time has no clocks.
  - Acknowledged at exactly the due time is met, a second later missed.
  - At exactly the due time an open clock is at risk with 0 minutes left, a second later breached.
  - At risk from less than a quarter of the window left, and minutes left are whole minutes.
  - → `test_incident_sla_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/sla.py` | 51 | `0 < value` to `0 <= value` | Equivalent: a value of 0 is taken as "no target" just before, so it never reaches this check. |

