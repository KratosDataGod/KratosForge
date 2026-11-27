# Mutation testing, batch 225

One module, run uncapped: every one of its mutants, not a sample of 150.
- Fabric runs from the Monitor page, notebooks published and run in Fabric, Fabric steps, metadata runs on Fabric capacity, table maintenance and Direct Lake models (`cloud_service.py`).

Batch 16 sampled 150 of this module's mutants then, 474 in all (71.3% before, 97.3% after), and batch 166 ran its 4 boundary mutants (50% before, 75% after; line 115 was left as timing only). The module has 516 mutants now. This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 516 of 516, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy. Each pytest run had a temporary folder of its own, made fresh by a local plugin and removed when the run ended, so another run's leftover files could not fail a mutant and no run left files behind. Three "killed" mutants picked at random (474, 137 and 104) were run again by hand, and each failed a real assertion or a refusal the test does not expect.

The "before" run used the tests that name the module: test_cloud, test_cloud_service_boundary, test_cloud_service_edges, test_cloud_service_more_edges, test_cloud_service_mutation, test_fabric_compute, test_fabric_step, test_failed_lookups and test_notebook_runs. They take about 30 seconds. Four more files were left out for time (the set with them took about 70 seconds, and the per-mutant timeout is 120 seconds): test_capacity_hard_stop_matrix, test_maintenance_entry_points, test_maintenance_entry_surfaces and test_meta_compaction. The "after" run adds batch 16's own extra files, test_notebook_files, test_notebook_publish, test_not_set_up, test_model_bpa, test_notebook_batch_edges and test_usage_import, and the new test_cloud_service_uncapped. Batch 16's test_dataops was left out for time (45 seconds on its own on the shared machine); the after set took about 60 seconds. The score counts only the files given. All of them pass in a copy of `sidecar/` on its own.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. No mutant timed out.

The "after" run reran the 123 survivors with `--only`. It was stopped after 63 of them, while more tests were still being written, and the rest were run again with the finished test file, in two parts (67, then the last 5). The first part's 56 kills came from tests that are all still in the finished file, unchanged.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cloud_service.py` | test_cloud, test_cloud_service_boundary, test_cloud_service_edges, test_cloud_service_more_edges, test_cloud_service_mutation, test_fabric_compute, test_fabric_step, test_failed_lookups, test_notebook_runs (+ test_notebook_files, test_notebook_publish, test_not_set_up, test_model_bpa, test_notebook_batch_edges, test_usage_import, test_cloud_service_uncapped) | 516 of 516 (512 + 4), uncapped | 393 / 123 | **76.2%** | 390/512 → 508/512 | 3/4 → 4/4 | 512 / 4 | **99.2%** | 4 equivalent |

Batch 16's extra files killed some of the 123 survivors and the new file the rest; how many each killed was not counted separately. Batch 166's line 115 boundary mutant (`>` to `>=` on Monitor's 30-second deadline) is now killed: a status asked exactly at the deadline is still asked.

### Bugs found

None. One thing was noted and left: `delete_notebook_schedule` answers with `notebook_schedules(name, lakehouse_ref)`, without the `workspace` it was given. The app always has the notebook saved with its workspace by the time it has a schedule (Publish saves it), so the answer is found from that; the test saves the notebook first, as the app does.

### What the survivors were, and the tests that now kill them

- **cloud_service.py**:
  - The capacity gate before table maintenance: no reading, Pass and Unknown let it start; Blocked stops it with the remedy in the message (and without one, no double space); a Warning not accepted, or accepted for an older reading, stops it; an accepted Warning is recorded first, and one that can't be recorded starts nothing.
  - Optimize's read-only check: each workspace's name (the environment's own and its extra workspaces), tables and reading, empty text when there is none, only the entities asked for, and "local" with nothing on this PC.
  - Optimize after a run: only tables a run wrote rows to, never the meta tables, and none for a OneLake environment with it switched off; the meta tables keep 7 days; a warned workspace without accepted risk ids says why it waits; on this PC the table's lease waits up to 120 seconds.
  - Monitor: 200 runs by default; a broken project says why it has no runs; the status refresh stops at the 30-second budget and after two unreachable answers in a row, and a run Fabric no longer knows resets that count; table health lists its environment's 20 newest maintenance jobs.
  - Publishing: the linked item is the one Publish writes whatever its name in Fabric, a deleted one falls back to the item with the name and says so, another item with the name is reported as one Publish would replace, a name given in Fabric stays unless the notebook was renamed here; a publish check with no known change says unknown; a publish or schedule with only a location goes to its workspace; two notebooks that would share an item name are refused naming the workspace.
  - Running and scheduling: the Parameters bar's values are sent only when there are some; a schedule without a time zone is in UTC; schedules are the linked item's; metadata schedules say whether the runner is published; only Dev's stores are made when missing.
  - Fabric steps: a save without an id is refused before anything starts; a failed step reads no table health; the V-Order cell rewrites only the picked tables unless asked; request ids are the time and six (steps) or four (metadata runs) hex digits; results are looked up among the 200 newest runs.
  - Metadata runs on Fabric capacity: a bundle without links or landing zones has neither in its config, also when a source lands files but no zones are known; a lost runner id publishes the bundle again; deleting a schedule answers with those left.
  - Direct Lake models: an endpoint without an id isn't ready; an existing model's names are read only when it is replaced; a model's id comes from the save, else by name, with its URL only when there is an id; the endpoint refresh is reported; a definition that can't be read stops the replace; a relationship to a table not written yet isn't listed as left out; the plan for an environment on this PC never asks Fabric, takes where tables are kept from the environment's name, finds the model by its name, reuses its last definition and says which tables are dimensions.
  - Profile: the preview shows at most 20 rows, rules that drop rows have no side-by-side preview, and the time taken is in hundredths; times between are rounded to milliseconds.
  - Cancelling marks only that run, and a run that already ended names itself.
  - → `test_cloud_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `cloud_service.py` | 240, 316 | `schemas = ... if lakehouse_ref else True` to `else False` (in `_publish` and `notebook_publish_check`) | Equivalent: with no lakehouse, `fabric_ipynb` returns before it reads `schemas_enabled`; it is used only for the shim cell, which needs a lakehouse. |
| `cloud_service.py` | 732 | the bundle upload's `max_workers=8` to `9` | Equivalent: every file is uploaded either way; only how many go at once changes. |
| `cloud_service.py` | 742 | `fabric_bundles.json` written with `indent=2` to `indent=3` | Equivalent: the file is only read back with `json`, so only its whitespace changes. |
