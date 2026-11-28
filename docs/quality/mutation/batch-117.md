# Mutation testing, batch 117

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Evidence from what the app already reads: the workspace and its items, role assignments, the capacity and the optimization engine's result (`excellence/adapters.py`).
- Where the optimization tables live, in OneLake or in a folder on this PC: listing tables and schemas, and adding columns (`optimize/lakehouse.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. Neither module has a boundary mutant. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

`optimize/lakehouse.py` is also reached through the Optimize service, by test_optimize_service, test_optimize_service_edges, test_access_cache, test_incident_desk, test_opportunities_share, test_tenant_history and the two earlier Optimize mutation files. All of them together take about 105 s, so the run used the five files that call the stores most directly (about 50 s). A mutant whose run times out counts as killed; none timed out.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/adapters.py` | test_excellence_catalog, test_excellence (+ test_excellence_adapters_mutation) | 55 of 55 (55 + 0) | 48 / 7 | **87.3%** | 48/55 → 55/55 | none | 55 / 0 | **100%** | none |
| `optimize/lakehouse.py` | test_optimize_lakehouse_stores, test_optimize_schema_v2, test_optimize_setup, test_optimize_shared, test_optimize_sync_states (+ test_optimize_lakehouse_mutation) | 41 of 41 (41 + 0) | 40 / 1 | **97.6%** | 40/41 → 41/41 | none | 41 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/adapters.py**:
  - A workspace without a display name is named by its `name`.
  - A missing description is empty text, and a given one is kept.
  - A listed item needs both an id and a type, and a missing item name is empty text.
  - A role whose principal has no id, or no role, reads as empty text.
  - A capacity's missing state, SKU and region read as empty text.
  - An unread peer without a name is named by its item id.
  - → `test_excellence_adapters_mutation.py`.
- **optimize/lakehouse.py**:
  - Adding columns returns the names of the columns the table didn't have, and none when it had them all.
  - → `test_optimize_lakehouse_mutation.py`.

### Left

None.

