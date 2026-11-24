# Mutation testing, batch 70

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The capacity settings history's app call: read the capacities the signed-in person can see, and their overrides for an administrator (`capacity_history_service.py`).
- The tenant settings history's app calls: read Fabric's tenant settings, and show what changed (`tenant_history_service.py`).
- Measured usage files as the app calls them: preview, import, list, remove and reconcile with this app's Fabric runs (`usage_service.py`).

The run was at `main` 8403031ca (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_opportunities.py` and `test_rpc_surface.py` also reach `usage_service.py`, but they read `docs/` or `app/`, so they were left out, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `capacity_history_service.py` | test_capacity_history (+ test_tenant_history_service_mutation) | 20 of 20 (20 + 0) | 19 / 1 | **95.0%** | 19/20 → 20/20 | none | 20 / 0 | **100%** | none |
| `tenant_history_service.py` | test_tenant_history, test_capacity_history (+ test_tenant_history_service_mutation) | 37 of 37 (36 + 1) | 27 / 10 | **73.0%** | 27/36 → 36/36 | 0/1 → 1/1 | 37 / 0 | **100%** | none |
| `usage_service.py` | test_usage_import, test_opportunities_share, test_estate_service_edges, test_risk_service_edges, test_platform_setup_steps, test_opportunities_assess_mutation (+ test_usage_service_mutation) | 31 of 31 (31 + 0) | 19 / 12 | **61.3%** | 19/31 → 31/31 | none | 31 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **capacity_history_service.py**:
  - A 401 is recorded as unreadable, like a 403, for the capacities and for an administrator's overrides; a 404 is a failed read.
  - → `test_tenant_history_service_mutation.py`.
- **tenant_history_service.py**:
  - A 401 or 403 is unreadable whether it comes as the status or only in the message; another status, or a number that only starts with 403, is a failed read.
  - Without a known tenant, a read asks the person to sign in again.
  - The history shows 200 changes by default, newest first, and says it is connected.
  - The lakehouse is read with schemas unless its connection says otherwise.
  - The last read is the newest successful one; a failure before it, or at the same moment, isn't a gap.
  - → `test_tenant_history_service_mutation.py`.
- **usage_service.py**:
  - A file name is cut to 200 characters, and a missing one reads as `usage.csv`.
  - A run names its workspace only with both an id and a name; without a metadata project the runs' names are kept, and a project's environment adds a name only when it has both, never over a run's own.
  - Only Fabric runs with a run id give estimates.
  - Capacity names map to their environments case-blind, and an environment without a capacity name is skipped.
  - The reconciliation reads 30 days by default, from up to 500 runs already on this PC.
  - → `test_usage_service_mutation.py`.

### Left

None.

