# Mutation testing, batch 321: workloads/query.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 145 ran all of its 109 mutants (89.0% before, 98.2% after).
- Views, search, grouping and paging over assessed workload records (`workloads/query.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` and `--cap 100000 --seed 7 --kinds all`, so all 109 of 109 mutants ran (109 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Temporary folders were handled and checked as in batch 320.

The set is the files that import the module: test_workload_query and test_workload_query_mutation. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/query.py` | test_workload_query, test_workload_query_mutation (+ test_workload_query_uncapped) | 109 of 109 (109 + 0) | 106 / 3 | **97.2%** | 106/109 → 107/109 | none | 107 / 2 | **98.2%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/query.py**: a list row keeps the workload's owners, and a workload without owners gives `{}` (`w.get("owners") or {}` to `and` gave `{}` for every row). → `test_workload_query_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/query.py` | 19 | `'dev': 2` to `3` | Equivalent, as in batch 145: Dev is still after Prod and Test and before any other environment. |
| `workloads/query.py` | 66 | the default order `9` to `10` | Equivalent, as in batch 145: it is still after Prod, Test and Dev. |
