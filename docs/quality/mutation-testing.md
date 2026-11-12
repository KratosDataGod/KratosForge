# Mutation testing, batch 1

How strong are the tests on the modules where a quiet bug costs the most? Line coverage says a line
ran; mutation testing says whether a test would notice if that line were wrong. This batch covers the
local Delta write path, the OneLake write queue, the local writes clear-up, the retry rules and the
redactor. Run at `main` 68b298c3f (2026-11-11).

## Method

- **Mutator.** A small AST mutator, run in batch 1 from outside the repository and since batch 2 kept
  as the dev-only script `sidecar/tools/mutate.py` (standard library and pytest only; the engine never
  imports it, so it adds no dependency). For each mutation point it rewrites the module with one
  change, runs the module's own test files with `pytest -x` and a 120 s timeout, and restores the
  file. mutmut was not used:
  it doesn't fit the sidecar layout (package under `sidecar/`, tests importing `fabric_desktop`
  from `PYTHONPATH`) without configuration that would live in the repository.
- **Mutations.** Comparison flips (`==`/`!=`, `<`/`>=`, `>`/`<=`, `is`/`is not`, `in`/`not in`),
  `and`/`or` swaps, negated `if`/`while`/conditional expressions, integer and boolean constants
  (0↔1, n→n+1, True↔False), a return value dropped to `None`, a `raise` removed, and `+`/`-`,
  `*`/`//` swaps. Docstrings are never mutated. Regular expressions are not mutated by this tool, so
  the redactor got a second run that deletes each secret pattern in turn (below).
- **Sampling.** At most 150 mutants a module, sampled with a fixed seed (7), so a re-run mutates the
  same points. Modules with fewer points are run in full.
- **Tests run against each mutant.** Only the module's own test files, listed in the table. A
  mutant that another file in the suite would catch still counts as surviving here: the point is
  whether the tests *about* the module pin its behavior.
- **Score** = (killed + timed out) / mutants run. A mutant that hangs the tests has been noticed,
  so a timeout counts as killed.
- **After.** The new tests were added to each module's set and the same sample re-run. The last
  few survivors in localdata/delta and retrying were then re-checked one by one after their final
  tests went in. Adding a test can't revive a killed mutant, so those re-checks give the final
  count.

## Results

| Module | Test files run | Mutants (of points) | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `redact.py` | test_redact, test_redactor_characterization, test_secret_detectors_userinfo, test_git_secrets_shapes, test_secrets_at_rest | 58 (58) | 58 / 0 | **100%** | no change needed | **100%** | 0 |
| `redact.py` `_SECRETS` (each pattern deleted) | same | 31 (31) | 31 / 0 | **100%** | no change needed | **100%** | 0 |
| `retrying.py` | test_retrying, test_retrying_more_edges, test_retry_after, test_remote_retry (+ test_retrying_mutation) | 150 (166) | 106 / 44 | **70.7%** | 149 / 1 | **99.3%** | 1 |
| `localdata/delta.py` | test_append_conversions, test_conversions_everywhere, test_local_delta_types, test_parse_errors_plain (+ test_localdata_delta_mutation) | 150 (322) | 115 / 35 | **76.7%** | 145 / 5 | **96.7%** | 5 |
| `write_queue.py` | test_write_queue_edges, test_onelake_writes (+ test_write_queue_mutation) | 94 (94) | 77 / 17 | **81.9%** | 91 / 3 | **96.8%** | 3 |
| `local_writes_service.py` | test_local_writes_clear, test_local_writes_service_edges, test_retention (+ test_local_writes_service_mutation) | 76 (76) | 69 / 7 | **90.8%** | 74 / 2 | **97.4%** | 2 |

Every survivor still left after this batch is an equivalent mutant (listed below): no change to
the code would make a test fail on it, because the mutant behaves the same as the original.

### What the survivors were, and the tests that now kill them

- **retrying.py (44).** The predicates that decide what is safe to send or write again had no direct
  tests in the module's own files: the Windows Delta log race (`os error 5`/`32` on `_delta_log`,
  and its staged `#n` commit form), a dropped data-file upload versus a dropped log commit, an
  expired write token, `is_retryable` and `status_of`. The exact transient status sets
  (`{408, 429, 500, 502, 503, 504}` and `{429, 502, 503, 504}`) and the backoff arithmetic
  (doubling, the cap, jitter scaling between half and full, the total cap on remote waits and on
  dropped connections) could also change unnoticed. `local_retry` could lose its attempt count, its
  waits or its "give up" raise. A wrong "retry" here writes an append twice; a wrong "don't" fails
  a write that would have gone in. → `tests/test_retrying_mutation.py`.
- **localdata/delta.py (35).** Name handling (`unquote` of doubled quotes, `split_name` of a
  three-part name, the reserved `__fd_` prefix), the text parser's OVERWRITE/append and BY NAME
  answers, the `CREATE ... USING DELTA` without `AS` refusal, `AS OF` placeholder numbering and
  aliasing, the 38-digit decimal limit, nullable columns in the Fabric-safe schema, an all-NULL
  column taking the table's type, `_assignable` for dates, lists, maps and structs, the order of
  `fits` problems, and `Conform`/`Counter`. → `tests/test_localdata_delta_mutation.py`.
- **write_queue.py (17).** The default of three uploads at once; a new queue holding zero bytes (one
  that started at 1 would make the first write as large as the cap wait forever); the fingerprint of
  no rows and its 32-character commit form; a write submitted while the worker idles; worker threads
  being daemons; a spill making its folder and removing its file without logging a failure; stats
  rounding. → `tests/test_write_queue_mutation.py`.
- **local_writes_service.py (7).** `pid_running` on POSIX must send signal 0 (any other number would
  signal, or end, someone else's process) and must answer False for a process that is gone; on
  Windows kernel32 must be loaded with `use_last_error=True` or the "access denied means it's running"
  check reads a stale error. "Reveal" must make a missing staging folder with its parents.
  → `tests/test_local_writes_service_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it can't be caught |
| --- | --- | --- | --- |
| retrying.py | 58 | `return min(default, cap)` after `when is None` dropped | `parsedate_to_datetime` raises on a bad date in every Python the app supports; it never returns None, so the line can't run. |
| localdata/delta.py | 175 | `isinstance(target, exp.Schema) and ...` → `or` | A plain `exp.Table` target's `this` is an identifier, never a table, so both forms give the same answer. |
| localdata/delta.py | 273, 287 | `lambda _: False` → `True` | Fallbacks for an older pyarrow without `is_string_view`/`is_list_view`; the installed pyarrow has them, so the fallback never runs. |
| localdata/delta.py | 452 | the outer `or` of the string/binary clause of `_assignable` → `and` (the inner `and` → `or` swaps are killed) | Unreachable: `is_string`/`is_binary` only match `pa.string()`/`pa.binary()`, so a pair that passes is already equal and returned by `src.equals(dst)` first. Dead code, not a bug. |
| localdata/delta.py | 486 | `zip(..., strict=True)` → `strict=False` | A short batch still fails in `RecordBatch.from_arrays` (an `ArrowInvalid`, itself a `ValueError`), so the write stops either way. |
| local_writes_service.py | 41 | `out["newest"] or 0` → `or 1` | Only the first file's time meets the default, and every real mtime is far above 1. |
| local_writes_service.py | 101 | `_usage(path, 0)` → `_usage(path, 1)` | That argument is the time used for ages; `_remove` reads only `bytes`. |
| write_queue.py | 30 | `if TYPE_CHECKING` negated | It only moves the pyarrow import to module load, so nothing a test can see changes. |
| write_queue.py | 227 | `self._running = 0` → `1` | `_running` is never read (a dead attribute). |
| write_queue.py | 245 | `return self._release(nbytes)` → `return None` | `_release` returns None. |

### Real bugs found

None. No surviving mutant showed wrong behavior: each was either untested behavior (now pinned) or
one of the equivalents above. Two pieces of dead code turned up and are noted rather than changed:
`Queue._running` in write_queue.py, and the string/binary clause of `_assignable` in
localdata/delta.py.

### Not in this batch

- `audit_chain.py`: an open pull request (#2305) changes it; it is left for the next batch so the
  tests are written against the code that lands.
- `delta_dml.py`: an open pull request (#2510) changes it; same reason.
- The policy and approval gates are covered by other work and were not touched.

## How to re-run

`sidecar/tools/mutate.py` (dev only: never run in CI, never imported by the engine) works in a
temporary copy of `sidecar/`, so a run in progress, or one stopped halfway, can't leave a mutant in
your working tree, and several modules can run at once. From `sidecar/`:

```sh
python tools/mutate.py fabric_desktop/retrying.py --out retrying.json -- \
  tests/test_retrying.py tests/test_retrying_more_edges.py tests/test_retry_after.py \
  tests/test_remote_retry.py tests/test_retrying_mutation.py
python tools/mutate.py fabric_desktop/redact.py --entries _SECRETS --out redact-entries.json -- \
  tests/test_redact.py tests/test_redactor_characterization.py tests/test_secret_detectors_userinfo.py \
  tests/test_git_secrets_shapes.py tests/test_secrets_at_rest.py
python tools/mutate.py fabric_desktop/delta_dml.py --only 186,222 --out recheck.json -- \
  tests/test_delta_dml_edges.py tests/test_delta_dml_mutation.py
```

Options: `--cap` (150) and `--seed` (7) choose the sample; `--entries NAME` deletes each entry of a
module-level list instead of mutating points; `--only` runs just the mutants with those indexes again,
to re-check survivors once tests are added. After `--`, the test files. Each line of output is
`<index> <line> <kind> <change> killed|survived|timeout`, and the `--out` JSON has the totals and every
result. Keep the seed at 7 to compare with the tables here: the script samples and mutates the same
points as the batch 1 scratch copy did. One module at a time takes 5 to 25
minutes on four cores; write_queue is the slowest, because a mutant that stops the queue's workers
hangs until the 120 s timeout. A timed-out run is stopped with every process it started (its own
process group, or `taskkill /T` on Windows). Caches, a local `.venv` and build output aren't copied
into the temporary `sidecar/`, and `--only` names any index that isn't in the run's sample instead of
quietly skipping it.

# Mutation testing, batch 2

The rest of the Delta write and commit paths: MERGE, UPDATE and DELETE (`delta_dml.py`), publishing a
table to OneLake (`publish.py`), table maintenance and the clear-up of stopped writes
(`maintenance.py`), the flush of a local commit to disk (`durable.py`), and a notebook's table writes
(`lakehouse.py`). Run at `main` fea0f02db (2026-11-11) with `sidecar/tools/mutate.py`, the same
method, sample cap and seed as batch 1.

## Results

| Module | Test files run | Mutants (of points) | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `delta_dml.py` | test_delta_dml_edges, test_local_delta_dml, test_merge_conversions, test_spark_merge (+ test_delta_dml_mutation) | 150 (223) | 126 / 24 | **84.0%** | 148 / 2 | **98.7%** | 2 |
| `publish.py` | test_publish_edges, test_onelake_writes, test_lease_before_commit, test_table_leases, test_write_token, test_ea_write_integrity (+ test_publish_mutation) | 120 (120) | 92 / 28 | **76.7%** | 117 / 3 | **97.5%** | 3 |
| `maintenance.py` | test_maintenance_edges, test_maintenance_concurrency, test_crash_leftovers, test_stopped_run_leftovers, test_meta_compaction (+ test_maintenance_mutation) | 150 (239) | 80 / 70 | **53.3%** | 139 / 11 | **92.7%** | 11 |
| `durable.py` | test_durable_commits (+ test_durable_mutation) | 28 (28) | 16 / 12 | **57.1%** | 28 / 0 | **100%** | 0 |
| `lakehouse.py` | test_lakehouse_write_edges, test_notebook, test_onelake_writes, test_write_types, test_append_callers | 150 (348) | 150 / 0 (1 timeout) | **100%** | no change needed | **100%** | 0 |

Every survivor still left is an equivalent mutant (below). The "after" numbers come from re-running
each module's survivors with `--only` once its new tests were in.

### What the survivors were, and the tests that now kill them

- **delta_dml.py (24).** The refusals named the schema instead of the table (`MERGE INTO None USING`
  for `UPDATE dbo.sales ... FROM`); UPDATE and DELETE could take the schema or nothing as the table's
  alias; a target that isn't a named table, an assignment to something that isn't a column, and a
  MERGE without USING or ON had no test of their own; a `THEN DO NOTHING` could be taken for DELETE;
  names handed to delta-rs could lose their quotes (delta-rs folds unquoted names to lower case);
  which source columns a MERGE copies as they are (for the EA-001 value check) wasn't pinned; an
  out-of-range value's message could lose Arrow's reason; and a change's counts could start at 1 or
  read the wrong metric. → `tests/test_delta_dml_mutation.py`.
- **publish.py (28).** Each staging folder name `stage_locally` refuses was only tested where another
  check caught it too, and a link out of the staging root wasn't tested; the staged table it returns,
  the schema modes that are allowed, `unchanged` answering False, an overwrite of the same rows being
  written again by default, the row fingerprint kept only on an overwrite, the predicate used only on
  an overwrite of a table already there, a write's own error being raised (removing that `raise`
  would have reported a failed write as written), an unknown byte count, a job's duration, and the
  type names in a refusal. → `tests/test_publish_mutation.py`.
- **maintenance.py (70).** The thresholds and defaults (32 MB, 20 files, 30 versions, 7 days and an
  hour, 30 days of log, an hour for a stale staged file, 200 footers); `health()` field by field (a
  missing table, no files, V-Order at the same time as the last write, a commit with no time,
  versions since VACUUM, the small-file and VACUUM advice at their exact thresholds, the leftovers
  advice's wording, the newest 200 commits); `retention_hours`, `check_retention`'s default and
  `kept_days` rounding up; VACUUM being a dry run in the preview and a real one in Compact (a preview
  that deleted files would have passed every test); Compact flushing only its own commits; a folder
  entry that vanishes or is a reparse point; the one-hour staleness; the refusals' empty answers;
  `_mb`. → `tests/test_maintenance_mutation.py`.
- **durable.py (12).** `_sync` was always replaced in the tests, so nothing checked that a file is
  opened for writing on Windows (FlushFileBuffers needs it) and read-only and non-blocking on POSIX,
  that a folder is flushed on POSIX and left to NTFS on Windows, that only a regular file (or a folder
  asked for as one) gets an fsync, or that `sync_commits` answers True and False.
  → `tests/test_durable_mutation.py`.
- **lakehouse.py.** Its own tests already killed every sampled mutant.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it can't be caught |
| --- | --- | --- | --- |
| delta_dml.py | 180 | `zip(..., strict=True)` → `strict=False` | The check just above refuses columns and values of different lengths. |
| delta_dml.py | 346 | back-cast `safe=False` → `safe=True` | The values already came through the safe cast to the table's type, so the cast back to their own type never refuses. |
| publish.py | 73 | `rmtree(here, ignore_errors=True)` → `False` | The folder was just made by `mkdtemp`, so removing it doesn't fail. |
| publish.py | 242 | `get_add_actions(flatten=True)` → `False` | Only `path` and `size_bytes` are read, which are the same either way. |
| publish.py | 247 | `zip(..., strict=True)` → `strict=False` | Both lists are columns of one table. |
| maintenance.py | 83 | `zip(..., strict=True)` → `strict=False` | Both lists are columns of one table. |
| maintenance.py | 111 | `.get("version", -1)` → `0` | The default is only read when `"version"` is missing, which the same line rules out. |
| maintenance.py | 114 | `max(files, 1)` → `max(files, 0)` | Only reached with 20 files or more. |
| maintenance.py | 199 | `raise_if_not_exists=False` → `True` | The retention keys are Delta's own table properties, which delta-rs sets whether or not the table has them yet (the tests that set retention on a new table pass either way). |
| maintenance.py | 230, 393 | `getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)` → `1` | The `stat` module defines that flag on every platform, so the default is never used. |
| maintenance.py | 227, 251, 398 | `stat(follow_symlinks=False)` → `True` | Links and reparse points were skipped before these are reached, so there is never a link to follow. |
| maintenance.py | 312 | `vacuum(retention_hours=0, ...)` → `1` | A file removed in the last hour is still named by its commit in `_delta_log`, which the same function reads. |
| maintenance.py | 362 | `history(20)` → `history(21)` | History is newest first: if the 21st commit is after the run started, so is the first. It differs only for a 21st commit with no time at all, which delta-rs and Spark don't write. |

### Real bugs found

- **A MERGE without ON fails with a Python error instead of saying it needs ON.** sqlglot gives a
  MERGE with no ON clause `on=False`, not None, so `delta_dml._merge`'s "MERGE INTO needs ON" check
  never fires, and `apply` then fails with `AttributeError: 'bool' object has no attribute 'copy'`.
  Nothing is written (it fails before delta-rs is called), so this is a wrong message, not lost data.
  Reported, not fixed here; the test above pins the message for a MERGE whose ON is missing outright.
- No other surviving mutant showed wrong behavior.

### Not in this batch

- `audit_chain.py` and `retention.py`: an open pull request (#2305) changes both; left so the tests
  are written against the code that lands.
- `localdata/delta.py` and `localdata/engine.py`: an open pull request (#2516) changes them
  (`localdata/delta.py` was in batch 1).
- The policy and approval gates, and `lakehouse.write_refusal` (the open metadata project's guard), are
  covered by other work and were not touched.

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
