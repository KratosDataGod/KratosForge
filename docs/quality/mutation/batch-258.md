# Mutation testing, batch 258: workload_service.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 196 sampled 150 of its then 449 mutants (70.0% before, 100% after).
- Workloads as the desktop UI lists them (`workload_service.py`): reading the metadata project, notebooks, pipelines and Fabric items into one inventory, a workload's page and timeline, the preflight and the actions, settling them from run records, and the audit lines sent to the platform.

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 449 of 449 mutants ran (444 point + 5 boundary). The mutants were split into shards with `--only` and merged, with the same temporary folders and checks as batch 256; a late kill (index 448) was run again by hand and fails a real assertion. Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used.

A mutant whose run times out counts as killed, as in earlier batches. Four timed out, before and after, and each was run again by hand without the 120 s limit: each hung until the tests' own per-test limit (`FD_TEST_TIME_LIMIT` in conftest.py, 300 s) stopped it, so each is a real hang, not a slow run. They are `_wl_record`'s `refresh` default made True (line 314), the preflight's risk check run when `assess` is false (line 579), the action's preflight asked to assess (line 584), and the audit send waiting for the lock instead of leaving its lines to the send already running (line 725).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workload_service.py` | test_workload_service_mutation, test_workload_service_paths, test_workloads, test_workload_pipelines, test_workload_audit, test_workload_query, test_workload_capacity_use (+ test_workload_service_uncapped) | 449 of 449 (444 + 5) | 439 / 10 | **97.8%** | 436/444 → 444/444 | 3/5 → 5/5 | 449 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workload_service.py**:
  - Audit lines go to the platform 50 at a time, oldest first.
  - Unlinking a notebook says which one.
  - A OneLake run log read exactly two minutes ago isn't reused; it is read again.
  - Capacity use counts a run that started exactly the period ago.
  - A run in Fabric needs a sign-in, names the workspace (or "its workspace"), and is risk-checked unless told not to.
  - Only an item Fabric refused to start (`run_item`, unauthorized) says "Not authorized"; another failure of `run_item`, or an unauthorized cancel, doesn't.
  - Only open actions are read for settling, so 200 newer settled ones don't crowd out an open one.
  - A schedule action is settled by the newest manual run.
  - → `test_workload_service_uncapped.py`.

### Left

None.

