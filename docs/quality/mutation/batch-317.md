# Mutation testing, batch 317: workloads/model.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Workload ids, run states, observations and how a read error is shown (`workloads/model.py`).

Batch 141 ran all 95 of its mutants then (88.4% before, 100% after). It still has 95, and this run ran all 95 of 95.

The run was at `main` ea988e99e (2026-11-20), with the method of batch 315. After the run, no folder in the `--basetemp` tree was nested 40 levels deep, and the kills don't end in a streak of near-identical times. Three kills picked at random were checked by hand, and each failed real tests (15, 1 and 3). No mutant timed out.

The set is the files that name the module, among them the read-error files (test_not_set_up, test_retrying, test_risk, test_typed_errors) that call `classify_error`. It runs in about 15 seconds. No `-k` filter or `--deselect` was needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/model.py` | test_workload_model_mutation, test_workloads, test_workload_query, test_workload_query_mutation, test_workload_pipelines, test_workload_service_paths, test_workload_service_mutation, test_workload_store_mutation, test_workload_audit, test_not_set_up, test_retrying, test_risk, test_typed_errors | 95 of 95 (94 + 1), uncapped | 95 / 0 | **100%** | no change needed | no change needed | 95 / 0 | **100%** | none |

No bugs were found, and no new tests were needed.

### Left

None.

