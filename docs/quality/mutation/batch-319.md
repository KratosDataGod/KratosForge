# Mutation testing, batch 319: workloads/store.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). It has no `<` or `>` comparison, so it has no boundary mutants.
- The management records this PC keeps about workloads (`workloads/store.py`): assignments and their history, links from notebooks to Fabric items, actions, lines waiting for the platform's audit, and the collection cache.

Batch 148 ran all 94 of its mutants then (81.9% before, 100% after). It still has 94, and this run ran all 94 of 94.

The run was at `main` ea988e99e (2026-11-20), with the method of batch 315. After the run, no folder in the `--basetemp` tree was nested 40 levels deep, and the kills don't end in a streak of near-identical times. Five kills were checked by hand, chosen because they change only a number or a flag: the actions kept (10000), the history's default limit (100), the action id's length (12), the 500 characters of a failed audit try's reason, and `exist_ok` when making the store's folder. Each failed real tests (1, 1, 1, 1 and 59). No mutant timed out.

The set is the files that name the module, among them the durability, crash and awkward-path files that open every store. It runs in about 20 seconds. No `-k` filter or `--deselect` was needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/store.py` | test_workload_store_mutation, test_workload_store_faults, test_sqlite_stores, test_store_upgrades, test_workload_audit, test_workloads, test_awkward_paths, test_sqlite_durability, test_store_crash_kills | 94 of 94 (94 + 0), uncapped | 94 / 0 | **100%** | no change needed | none | 94 / 0 | **100%** | none |

No bugs were found, and no new tests were needed.

### Left

None.
