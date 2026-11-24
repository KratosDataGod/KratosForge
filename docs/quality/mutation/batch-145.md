# Mutation testing, batch 145

Views, search, grouping and paging over workload records (`workloads/query.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 109 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/query.py` | test_workload_query, test_workloads (+ test_workload_query_mutation) | 109 of 109 (109 + 0) | 97 / 12 | **89.0%** | 97/109 → 107/109 | none | 107 / 2 | **98.2%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/query.py**:
  - Each row lands in its own environment group. The existing test grouped one Fabric-only and one PC-only workload, so swapping the two groups gave the same list.
  - Prod comes before Test whatever order the deployments are in.
  - The default page is 100 rows.
  - Nobody signed in owns nothing (`False`, not just falsy).
  - A row without tags or actions has empty lists.
  - Search finds a deployment's environment and its label.
  - → `test_workload_query_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/query.py` | 19 | `'dev': 2` to `3` | Equivalent: Dev is still after Prod and Test and before any other environment. |
| `workloads/query.py` | 66 | the default order `9` to `10` | Equivalent: it is still after Prod, Test and Dev. |

