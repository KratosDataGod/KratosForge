# Mutation testing, batch 3

The local engine's statement, write and commit side: running a script and its Delta writes
(`localdata/engine.py`), the local Delta write path again now that #2516 changed it
(`localdata/delta.py`), T-SQL on this PC (`localdata/tsql.py`), the safe SQL parser
(`sql_dialects.py`), local SQL transformations (`localdata/transform.py`), the list of local
projects (`localdata/projects.py`), and the shared JSON file writer every saved setting goes through
(`jsonfile.py`). Run at `main` e26ff6876 (2026-11-12) with `sidecar/tools/mutate.py`, the same method,
sample cap and seed as batches 1 and 2.

## Results

| Module | Test files run | Mutants (of points) | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent or left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/engine.py` | test_local_data, test_local_data_engine_edges, test_local_data_engine_more_edges, test_duckdb2_shapes, test_localdata_long_paths, test_spill_leftovers, test_local_delta, test_local_delta_dml, test_local_explain, test_awkward_paths, test_local_transform, test_localdata_manager_edges, test_safe_parse_readers (+ test_localdata_engine_mutation) | 150 (859) | 107 / 43 (2 timeouts) | **71.3%** | 140 / 10 | **93.3%** | 8 equivalent, 2 left |
| `localdata/delta.py` | batch 1's set, test_localdata_delta_mutation, test_safe_parse_readers | 150 (322) | 145 / 5 | **96.7%** | no change needed | **96.7%** | 5 (batch 1's) |
| `localdata/tsql.py` | test_local_tsql, test_local_tsql_more_edges, test_sql_lambdas, test_tsql_loud_gaps | 150 (694) | 150 / 0 | **100%** | no change needed | **100%** | 0 |
| `sql_dialects.py` | test_parse_errors_plain, test_safe_parse_readers, test_snowflake_sql_variadic, test_sql_editor | 27 (27) | 27 / 0 | **100%** | no change needed | **100%** | 0 |
| `localdata/transform.py` | test_local_transform, test_local_transform_edges (+ test_local_transform_mutation) | 150 (170) | 122 / 28 | **81.3%** | 146 / 4 | **97.3%** | 4 |
| `localdata/projects.py` | test_local_data_projects_edges, test_corrupt_state_reads, test_file_replace (+ test_localdata_projects_mutation) | 64 (64) | 44 / 20 (3 timeouts) | **68.8%** | 64 / 0 | **100%** | 0 |
| `jsonfile.py` | test_json_files, test_file_replace, test_state_writes, test_meta_atomic_save (+ test_jsonfile_mutation) | 40 (40) | 31 / 9 | **77.5%** | 38 / 2 | **95.0%** | 2 |

`localdata/engine.py`'s test set leaves out the slowest files that also drive it (test_sql_exec,
test_local_sql_stop, test_local_tsql, test_localdata_kills): with them each surviving mutant took about
two minutes. The "after" numbers come from re-running each module's survivors with `--only` once its
new tests were in. `localdata/delta.py` scored the same as in batch 1 after #2516, with the same five
equivalent mutants.

### What the survivors were, and the tests that now kill them

- **localdata/engine.py (33 killed).** A `limit` of 0 could return a row; statement numbers could
  count from -1; the seconds that `write_delta`, `make_table` and `change_delta` report weren't held
  to three places; a script mixing a Delta write and another statement could be refused as unreadable;
  a dry run's "doesn't fit" message could come back wrapped as "Writing ... failed"; a change to a
  table Fabric couldn't read could go ahead (removing that `raise` passed every test); which versions a
  change flushes to disk; `delta_history` with a limit of 0, its `own` flag, and a folder of Parquet
  files asking for versions; reading as of a time before the first version; a folder table listing at
  most 50 paths; the automatic look for changed files forcing a full refresh; spill leftovers that are
  folders or links; the 64 characters kept for a data file's name and 3 per partition in the
  long-path check; an unreadable log counting as maybe committed; the Fabric check's 20 findings;
  `_collect` with no result; `_short`'s 1,000 characters; and the explain plan's estimates, details,
  memory and spill totals, zero-time shares and indentation. → `tests/test_localdata_engine_mutation.py`.
- **localdata/transform.py (24).** A comment line before the settings ended them; a CTE could be read
  as a table; a bad default's message could name the wrong thing; the plan could drop a broken step;
  a run's start and end times, its rounded seconds, the run files' names, newest-first listing, the
  default of 20, a limit of 0 still giving one, and the 50 runs kept; a run in a project with no
  transforms folder; the state file's layout; a dry run planning each query with `LIMIT 0`, no row
  count and (for Spark SQL) an alias; marking a table without counting its rows; a Delta step's
  `version:N` mark, a Delta step written another way since being refused, and a dry run treating a
  built Delta step as built. → `tests/test_local_transform_mutation.py`.
- **localdata/projects.py (20).** The default settings, a time limit of exactly a week, where a project
  keeps its database and spills, finding a registered folder by its normalized path, the summary, an
  unknown project raising KeyError, a list file that can't be opened, ids counting up from 2, and a
  project kept in a folder that isn't made yet or one that is already there and empty.
  → `tests/test_localdata_projects_mutation.py`.
- **jsonfile.py (7).** The two-space indent, making missing folders and reusing existing ones for text
  and bytes, a failed swap of bytes raising and leaving no temporary file (removing that `raise` passed
  every test), a missing file reading as the default itself, and pruning set-aside copies that have no
  `-wal` or `-shm` without logging a failure. → `tests/test_jsonfile_mutation.py`.
- **localdata/tsql.py, sql_dialects.py, localdata/delta.py.** Their own tests already killed every
  sampled mutant that can be caught.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it can't be caught |
| --- | --- | --- | --- |
| localdata/engine.py | 140 | `min(..., default=0)` → `default=1` | A Delta table always has at least one commit, so the default is never used. |
| localdata/engine.py | 379 | `return False` → `return None` | The caller only tests it for truth. |
| localdata/engine.py | 548 | `if not query.__exit__(...)` negated | Both branches raise the same error. |
| localdata/engine.py | 576 | `lazy=False` → `True` (the default) | The rows are the same either way; only the memory a large result uses differs. |
| localdata/engine.py | 748 | `len(texts) > 1` → `> 0` | One statement was already checked by DuckDB when the script was split. |
| localdata/engine.py | 965 | `entry is not None and ...` → `or` | A table folder with a Delta log is always in the catalog (`_refresh_own` adds it), so an entry is never missing for one. |
| localdata/engine.py | 987, 988 | `result is None or not result.description` → `and`, and its `raise` removed | Only one SELECT is accepted (checked just before), and a SELECT always has a description. |
| localdata/transform.py | 253 | `return []` → `None` | The caller only tests it for truth. |
| localdata/transform.py | 274 | `delta_history(name, 1)` → `0` | `delta_history` reads at least one version. |
| localdata/transform.py | 307 | `mkdir(parents=True)` → `False` | The transforms folder's parent is the project folder, which always exists. |
| localdata/transform.py | 315 | `unlink(missing_ok=True)` → `False` | The file was just listed. |
| jsonfile.py | 49, 64 | `unlink(missing_ok=True)` → `False` | The temporary file is made before the `try`, so it exists when a failed write removes it. |

**Left, not equivalent:** `engine.py` 539, `execute_reader` with `lazy=False`, gives the same rows
but copies a whole result into memory before the first batch (#1340). Catching that needs a memory
measurement, not a unit test. `engine.py` 1262, the Stop thread not being a daemon, only matters when
the process exits while a query is being stopped.

### Real bugs found

- None. Every killed survivor was a missing test, not wrong behavior.

### Not in this batch

- `delta_dml.py`: #2528 changed it while this batch ran, so its re-score is left for the next batch
  (batch 2 scored it at 98.7%).
- `audit_chain.py` and `retention.py`: still changed by an open pull request (#2305).
- The policy and approval gates were not touched.

