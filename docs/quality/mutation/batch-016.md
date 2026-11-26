# Mutation testing, batch 16

Three modules no open pull request is changing:
- The TPC-H benchmark notebooks: the 22 queries, the notebooks' cells, and writing them as `.ipynb` files (`benchmark.py`).
- Proving a notebook and replaying a failure: a proof's two runs, how they are compared, its verdict and receipt, proof rules, and opening, checking and saving replay bundles (`prove_service.py`).
- The Fabric side of the app: publishing notebooks and their schedules, the Fabric runs log and Monitor, profiling, the Direct Lake model, health and table maintenance, and Fabric steps (`cloud_service.py`).

The run was at `main` 577b43e64 (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 13 (sample cap 150, seed 7), on DuckDB 1.5.6. `benchmark.py` has 41 points, so all of them were run. The other two have more points than the cap, so 150 of each were run. It ran beside batch 17, which took other modules.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batches 11 to 15. Every test passed both ways, so no run below can score a false 100%. One test was left out of the `benchmark.py` runs because it reads the repository's `examples/` folder: `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks`.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `benchmark.py` | test_side_by_side, test_receipts_env (+ test_benchmark_mutation) | 41 of 41 | 27 / 14 | **65.9%** | 41 / 0 | **100%** | none |
| `prove_service.py` | test_prove, test_prove_service_edges, test_local_retention_gaps (+ test_replay, test_replay_edges, test_receipts_edges, test_debugging, test_wizard, test_prove_service_mutation) | 150 of 515 | 75 / 75 | **50.0%** | 146 / 4 | **97.3%** | 4 equivalent |
| `cloud_service.py` | test_cloud, test_cloud_service_edges, test_cloud_service_more_edges, test_fabric_compute, test_fabric_step, test_failed_lookups, test_notebook_runs (+ test_notebook_files, test_notebook_publish, test_not_set_up, test_model_bpa, test_dataops, test_notebook_batch_edges, test_usage_import, test_cloud_service_mutation) | 150 of 474 | 107 / 43 | **71.3%** | 146 / 4 | **97.3%** | 2 equivalent, 2 with reasons below |

The one timeout, in `cloud_service.py`, is a real hang: with the condition turned round, Monitor builds a Fabric client when there is no sign-in, and the status check then waits on the network.

Existing test files the first sets left out were tried on the survivors first:
- `benchmark.py`: no other test file calls it.
- `prove_service.py`: test_replay, test_replay_edges, test_receipts_edges, test_debugging, test_wizard. test_replay was left out of the first set because it takes most of a minute on its own. They killed 28: a replayed table's key; what blocks a replay (the kernel's own process, another lakehouse); the live replay, its result and its end; a policy against data samples in bundles; the bundle size limit; whether an opened bundle keeps its lakehouse; a replay notebook's cells, proofs and publishing waiting for its replay; samples and pinned tables read with the short limits, and a sample read offline; the sandboxes a replay remembers; a vacuumed version used only by choice; seeding a table's sample; the names of an opened and a saved bundle; and a wizard notebook whose name is taken.
- `cloud_service.py`: test_notebook_files, test_notebook_publish, test_not_set_up, test_model_bpa, test_dataops, test_notebook_batch_edges, test_usage_import. They killed 10 and timed out on 1: where a notebook is published when no workspace is picked, its schedules after a publish, a Fabric item changed since the last publish (and one made by another notebook), the message when the Fabric notebook published to before was deleted, the name a linked notebook publishes under, and the runs list when the metadata project can't be read.

The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **benchmark.py**:
  - The queries numbered 1 to 22, from the pricing summary to the country codes.
  - The queries cell being the only hidden one, with the 22 queries in order.
  - `write()` making a nested folder that isn't there yet, returning both paths, and writing JSON with an indent of 1 and one closing newline.
  - `write()` taking a folder as text and writing over its own files the second time.
  - Running the module writing to the folder given, or to the current folder when none is, and printing each path.
  - → `test_benchmark_mutation.py`.
- **prove_service.py**:
  - A proof waiting up to six hours for Fabric.
  - The Fabric job's own time and the time Fabric took to start, a failure reason given as text, and a job without times having no job time.
  - Deleting a proof's outputs returning the folder deleted.
  - Verifying a table again saving a receipt only when asked, with its verdict and wording.
  - A receipt that can't be saved saying why in the proof's message.
  - The plan naming the workspace and lakehouse, or saying "its workspace" and "the lakehouse" when they have no name.
  - Comparing the two runs: more than five changed tables cut short with dots, a table written on one side only saying which, and the two runtimes' differences listed only when Fabric's is known.
  - A proof's receipt keeping its parameters, inputs, environment, both runs, its outputs without their rules, the rules, the cost and every limitation.
  - The verdict: what stopped a run on this PC, never blamed for a Fabric failure; "fingerprint" when any table was compared by checksum only; "table" for one and "tables" for more.
  - Proof rules keeping their default and keying each table as schema.table, and saving them returning them.
  - The Variables pane keeping its saved watches when they are left out.
  - Receipts exported as HTML and saved under new names in a nested Downloads folder, and a receipt file never saved with parts left out.
  - Replay bundles: ending when no replay runs, an opened bundle's 16-character id, its safe name and its lakehouse, a bundle that doesn't say whether it has ids counting as having them, the check naming the bundle's lakehouse or saying "another lakehouse" and "its lakehouse", a pinned table read with the short limits when signed in, and a saved bundle's safe file name.
  - → `test_prove_service_mutation.py`.
- **cloud_service.py**:
  - The Fabric runs log being made in a nested app folder, listing the newest 50 by default and keeping the last 500.
  - Monitor counting Fabric as unreachable again from nothing after one answer, keeping a run's message when a check fails, and getting a Fabric client only when already signed in.
  - The model link opening the model's details in Fabric.
  - Profiling the source or the table written, and a written table only getting check suggestions.
  - Preview showing only the source rows a rule changes.
  - A schedule never replacing changes made in Fabric.
  - A publish Fabric returns no id for not being recorded as published.
  - A Fabric run counted as the engine it was published for (Python or Spark).
  - A linked notebook with another name not blocking a publish, and one with the same name in another folder blocking it.
  - A Fabric step whose item comes back without an id being looked up by name.
  - Table maintenance without a layer retention keeping the default days.
  - Building the Direct Lake model over an existing one asking first and naming it, even when Fabric gives no id.
  - Health listing only this environment's table maintenance and looking at the last 100 runs.
  - An environment with no target being in OneLake, so it can run in Fabric.
  - A Fabric step's and a metadata run's result being the run asked for, looked for in the last 200 runs.
  - A table with one V-Ordered file counting as V-Ordered.
  - Publishing the Fabric bundle recording it in an app folder whose parent isn't there yet.
  - → `test_cloud_service_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `cloud_service.py` | 327 | the publish check's schema fallback `True` → `False` when there is no lakehouse | Equivalent: with no lakehouse the notebook is published without the lakehouse cell, the only part that reads it. |
| `cloud_service.py` | 558 | the compaction lease's `wait=120` → `121` | A second more on a lease no test waits out. |
| `cloud_service.py` | 684 | the bundles file's `indent=2` → `3` | Equivalent: the file is only read back as JSON. |
| `cloud_service.py` | 744 | a schedule's store `create=env == "dev"` → `!=` | The tests fake the metadata store, which doesn't make anything, so whether it would be created isn't seen; a real one needs OneLake. |
| `prove_service.py` | 258 | an unreadable bundle's copy removed with `missing_ok=True` → `False` | Equivalent: the copy was written just before, so it is always there to remove. |
| `prove_service.py` | 816 | the first poll after a cell starts asks from version `-1` → `0` | Equivalent: at version 0 nothing has been output yet, so both give no outputs; after it, both give them all. |
| `prove_service.py` | 817 | `snap.get("outputs") or []` → `and []` on that first poll | Equivalent in the record: it changes only what the panel shows for a running cell until the next poll, a second later, and the cell's final outputs come from a poll of their own. |
| `prove_service.py` | 825 | the final `snap.get("outputs") or outputs` → `and outputs` | Equivalent: the outputs seen last while the cell ran are the final poll's outputs, and when there are none both are empty. |

### Real bugs found

None.

### Not in this batch

- `benchmark.py` is done. `prove_service.py` and `cloud_service.py` past the 150 sampled mutants.
- `git/repo.py`, which wasn't needed because none of the three was skipped.

