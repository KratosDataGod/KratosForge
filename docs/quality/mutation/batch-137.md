# Mutation testing, batch 137

One workload module not covered before, run with `--kinds all` (it has no boundary mutants):
- A workload's health, readiness and findings, each with its evidence (`workloads/health.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/health.py` | test_workload_health, test_workloads (+ test_workload_health_mutation) | 91 of 91 (91 + 0) | 84 / 7 | **92.3%** | 84/91 → 91/91 | none | 91 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/health.py**:
  - A workload is partly observed when one deployment was read and another wasn't; not when all or none were, and not when the unread one has a last run.
  - A definition problem alone doesn't make a workload that runs well need attention; it only blocks readiness.
  - A run waiting for a decision has a default text, and a cancelled run has an empty one unless it has a message.
  - An older version is named by eight characters of each hash.
  - → `test_workload_health_mutation.py`.

### Left

None.

