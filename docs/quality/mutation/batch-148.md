# Mutation testing, batch 148

The management records this PC keeps about workloads (`workloads/store.py`): assignments and their history, links from notebooks to Fabric items, actions, lines waiting for the platform's audit, and the collection cache.

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 94 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/store.py` | test_workloads, test_workload_store_faults, test_sqlite_stores, test_store_upgrades, test_workload_audit (+ test_workload_store_mutation) | 94 of 94 (94 + 0) | 77 / 17 | **81.9%** | 77/94 → 94/94 | none | 94 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/store.py**:
  - The schema version is 1 and 10,000 actions are kept.
  - The file's folder is made with its parents.
  - Assigning returns what the workload now has.
  - History shows the newest 100 by default; actions and waiting audit lines page at 50.
  - A link with a part missing is not kept. The same item again writes no history, and another item does.
  - Unlinking a notebook that isn't linked gives empty text.
  - An action that doesn't exist is a `KeyError`. An action has a 12-digit id, and ends only with an ending status.
  - A reference is merged into the one kept.
  - A failed audit try keeps 500 characters of why.
  - → `test_workload_store_mutation.py`.

### Left

None.

