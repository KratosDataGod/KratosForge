# Mutation testing, batch 141

One workload module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- Workload ids, run states, observations and how a read error is shown (`workloads/model.py`).

The run was at `main` 4ddd3662 (2026-11-18), with the same tool and method as batch 140. The module has fewer than 150 mutants, so all of them ran. `test_workloads.py::test_ten_thousand_workloads_filter_and_page_quickly` was left out with `--deselect`, as in batch 140, to keep each mutant's run short. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/model.py` | test_workloads, test_workload_query, test_not_set_up, test_typed_errors, test_retrying (+ test_workload_model_mutation) | 95 of 95 (94 + 1) | 84 / 11 | **88.4%** | 84/94 → 94/94 | 0/1 → 1/1 | 95 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/model.py**:
  - Every kind of id reads back, and an id missing a part (a project, entity, workspace, item, name or path) isn't one.
  - A time given as seconds is UTC, a naive time is read as UTC, and an aware one keeps its offset.
  - An unknown observation state is refused.
  - A cached read exactly its maximum age old is still cached.
  - A 401, 403 or 429 status is read from the error itself, not from its words.
  - → `test_workload_model_mutation.py`.

### Left

None.

