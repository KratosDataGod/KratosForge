# Mutation testing, batch 180

One module, run uncapped: every mutant, not a sample of 150. Batch 14 sampled 150 of its then 373 mutants.
- Where a metadata project's tables live: writes and appends that lose a race for the next version, streams and the files a failed one leaves, MERGE, reads from the Delta log, the run metadata tables and their compaction, and files on this PC and in OneLake (`meta/store.py`).

The run was at `main` 9d291bdeb (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902. It used `--cap 100000 --seed 7 --kinds all`, so all 381 mutants ran (379 point, 2 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. As in batch 178, each pytest session got a new, empty base folder of its own. A mutant whose run times out counts as killed; none timed out.

The store's tests take several minutes together, far over the mutator's 120-second limit for one mutant. So the "before" figures come from four runs, as in batch 95:
- All 381 mutants ran with the 13 quicker files that call the store directly (the first 13 in the table, about 20 seconds together). 63 survived.
- The 63 ran again with group A, the other files that call the store and run in under 10 seconds each: test_crash_kills, test_batches, test_meta_compaction, test_landing_manifest, test_maintenance_concurrency, test_dataops_edges, test_delta_profile_matrix, _modern, _readers, test_delta_read_spark_tables, test_meta_service_edges, test_meta_keys_mutation and test_maintenance_entry_points. Group A killed 21: how often meta tables are compacted, the lease-lost note, reading a table with deletion vectors, whether a table exists and its version, which stores remove what a failed stream left, sync conflicts, the compaction lease, OneLake's file list, its downloads and its file client, and the cleanup notes.
- The 42 left ran with group B, the slow concurrency and DataOps files: test_concurrent_writers, test_dataops and test_large_tables. Group B killed 6: sync conflicts, which writes may move past other writers' commits, and reading CSV dates.
- The 36 left ran with group C: test_keys, test_delta_step_kills and test_optimize_setup. Group C killed 5: the row count and highest value from the log's statistics, and a table created in a folder that is already there.

Two tests in `test_delta_profile_matrix.py` read `docs/`, so they were left out with `--deselect`. Mutants killed in any of the four runs count as killed "before". The "after" run gave the 31 survivors the first set and the new file. The groups weren't needed there.

Before the "after" run, the new tests were run in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/store.py` | test_meta_store_mutation, test_meta_store_edges, test_meta_write_types, test_durable_commits, test_lease_before_commit, test_delta_profile_writes, test_landing_zones, test_bounded_previews, test_conversions_everywhere, test_full_load_memory, test_meta_keys_edges, test_awkward_paths, test_meta_filtered_reads, then groups A, B and C above (+ test_meta_store_uncapped) | 381 of 381 (379 + 2), uncapped | 350 / 31 | **91.9%** | 350/379 → 370/379 | 0/2 → 1/2 | 371 / 10 | **97.4%** | 8 equivalent, 2 not killed |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/store.py**:
  - Only an append may move up 100 versions past other writers' commits.
  - Only a store on this PC makes a table's folder before writing it, for a write, a stream and a create. A store elsewhere never makes a local folder.
  - A first write whose lease is gone raises `LeaseLost`, not a conflict.
  - An append made again after losing a race is still refused when it would change values. One made again after the table went away raises the conflict.
  - `merge_insert` returns the new version, and refuses a version that another writer has moved on from.
  - History gives the last 20 commits by default. `has_commit` looks only at commits after `since`.
  - CSV files infer types from their first 10,000 rows.
  - A first write that failed has its files removed. Data files are the files outside the log, and removing one already gone is no error.
  - A meta table is compacted at every 100th commit and never at version 0, without waiting for the compaction lease. The compaction syncs its commit from the next version. The compaction lease lasts 120 seconds.
  - Some of the new tests also pin what groups A to C kill (the row count from the log, sync conflicts, OneLake's files), so the store's quick set now catches those too.
  - → `test_meta_store_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/store.py` | 137 | `options is None and "://" not in uri` to `or` | Equivalent: a store on this PC has no storage options and a local path, and a OneLake store has both, so `and` and `or` agree for every store. |
| `meta/store.py` | 272 | the row count's `get_add_actions(flatten=True)` to `False` | Equivalent: in deltalake 1.x, `num_records` is a top-level column either way. |
| `meta/store.py` | 343 (2) | the cast before an append made again: negated, and `!=` to `==` | Equivalent: the rows were already made Fabric-safe before the first try. `delta_safe` gives a writable schema back as it is, so the retry's safe schema always equals the rows' schema. |
| `meta/store.py` | 402 | `_delta_safe`'s `loaded` default `False` to `True` | Equivalent: the only call that leaves `loaded` out is `write_stream`'s, with `append=False`, and then the table is never loaded. |
| `meta/store.py` | 645 | `t.version() <= since` to `<` | Equivalent: when `since` is the latest version, the mutant asks for `history(0)`, which gives no commits, so the answer is `False` either way. |
| `meta/store.py` | 726 | `version.group(1)` to `group(0)` | Equivalent: the pattern is one group of 20 digits matched at the start, so the whole match and the group are the same text. |
| `meta/store.py` | 792 | `version <= 0` to `<= 1` before a meta compaction | Equivalent: version 1 isn't a multiple of 100, so it is skipped either way. |
| `meta/store.py` | 692, 701 | `sort(..., maintain_order=True)` to `False` for meta tables | Not killed: Polars 1.x sorted 3,000 rows with one shared time in their order either way, so no test here could tell the two apart. |

