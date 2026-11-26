# Mutation testing, batch 5

The small modules that guard data integrity and reliability around the local engine and the data folder:
- The Fabric-safe Delta profile check (`delta_profile.py`).
- Reading rows and Arrow readers the same way on DuckDB 1.x and 2.0 (`duckdb_rows.py`).
- The local engines and background queries (`localdata/manager.py`).
- Telling a full or read-only disk apart from other failures, and saying so (`disk_full.py`).
- The guard on paged service reads (`paging.py`).
- The cached SQL endpoint schemas (`schema_cache.py`).
- The data folder's format mark, which keeps an older build from opening newer data (`data_format.py`).

The run was at `main` b96c80215 (2026-11-12), with `sidecar/tools/mutate.py` and the same method, sample cap and seed as batches 1 to 4. Every module has fewer points than the cap, so every mutant was run.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `delta_profile.py` | test_delta_profile_writes, test_local_delta, test_schema_evolution (+ test_delta_profile_mutation) | 18 | 17 / 1 | **94.4%** | 18 / 0 | **100%** | 0 |
| `duckdb_rows.py` | test_duckdb_rows, test_duckdb_rows_edges, test_duckdb_try_cast (+ test_duckdb_rows_mutation) | 29 | 26 / 3 | **89.7%** | 29 / 0 | **100%** | 0 |
| `localdata/manager.py` | test_local_data, test_local_data_calls, test_local_sql_stop (+ test_localdata_manager_mutation) | 51 | 38 / 13 | **74.5%** | 51 / 0 | **100%** | 0 |
| `disk_full.py` | test_disk_full, test_disk_full_windows, test_out_of_memory (+ test_disk_full_mutation) | 72 | 25 / 47 | **34.7%** | 71 / 1 | **98.6%** | 1 |
| `paging.py` | test_paging, test_paging_complete (+ test_paging_mutation) | 12 | 11 / 1 | **91.7%** | 12 / 0 | **100%** | 0 |
| `schema_cache.py` | test_schema_cache, test_attach_race (+ test_schema_cache_mutation) | 11 | 10 / 1 | **90.9%** | 11 / 0 | **100%** | 0 |
| `data_format.py` | test_data_format (+ test_data_format_mutation) | 17 | 16 / 1 | **94.1%** | 17 / 0 | **100%** | 0 |

The "after" numbers are full re-runs with the new tests in, except `disk_full.py`, whose last five survivors were run again with `--only`.

`disk_full.py` started low because its own test files check the messages end to end through a few real failures. Most of its branches (each Windows error code, each text, the chain limit, the sizes, the data folder's refusals) were only reached from other modules' tests, if at all.

### What the survivors were, and the tests that now kill them

- **delta_profile.py**: the writer-version limit raised from 2 to 3 passed. A table at writer version 3 would then have been written as Fabric-safe. → `test_delta_profile_mutation.py` pins both limits at their edges.
- **duckdb_rows.py**: on the pinned DuckDB 1.x, nothing checked that 2.0 gets a relation (`con.sql`) and 1.x an executed result. Nothing checked either that a result with only `to_arrow_reader` is read through it. → stand-in connections and results pin both branches.
- **localdata/manager.py**:
  - A folder added twice.
  - A folder whose first look fails, which must be taken back off the project.
  - The status of an unknown query.
  - The elapsed time's arithmetic and rounding.
  - Cancelling an unknown or finished query.
  - The ten-minute keep for finished results, at its edge.
  - The background thread's name and daemon flag.
  - → `test_localdata_manager_mutation.py`. The clock and thread are replaced only as the module's own names.
- **disk_full.py**:
  - Both Windows disk-full codes and both refusal codes.
  - Each errno and each text.
  - The 20-deep chain limit and a cycle in the chain.
  - Free space walking up to a folder that exists.
  - Every size unit at its edge.
  - Each message with and without free space.
  - A data-folder refusal inside versus outside the folder.
  - A full disk already reported.
  - The bool results.
  - → `test_disk_full_mutation.py` (one Windows-only test, for a drive letter).
- **paging.py**: the 10,000-page limit raised by one. → a test follows 9,999 links and is refused on the next.
- **schema_cache.py**: the 64-character cut of a lakehouse id in its file name. → pinned with a 70-character id.
- **data_format.py**: a folder already at this build's format was written again, which replaced the record of the build that marked it. → pinned: the mark is left as it was.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it can't be caught |
| --- | --- | --- | --- |
| disk_full.py | 72 | `_size`'s last `return` → `None` | Unreachable: the loop always returns at "GB" (`# pragma: no cover`). |

### Review follow-ups in this batch

- `tests/test_leases_mutation.py` (batch 4) no longer patches the process-wide `time.sleep`. Three tests now replace only `leases`' own `time` name with a stand-in whose `sleep` does nothing.
- `localdata/engine.py`: when a refused or failed Delta write closes its reader (#2542), a close that fails is now suppressed, so it can't replace the error that stopped the write. This is the batch's only product-code change: one line, on the error path only. `tests/test_localdata_engine_refused_write.py` checks it with a reader whose `close()` raises. It passes on DuckDB 1.5.6 and 2.0.

### Real bugs found

- None. Every killed survivor was a missing test, not wrong behavior.

### Not in this batch

- `audit_chain.py` and `retention.py`, which an open pull request (#2305) still changes.
- `sealed_settings.py`, which belongs to the policy settings.
- The policy and approval gates.

