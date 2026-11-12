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
process group, or `taskkill /T` on Windows); a kill that hasn't finished within 30 s is given up on,
so one mutant can't hang the run. Caches (anywhere) and a local `.venv` and build output (at the top of
`sidecar/` only, so a test fixture folder of the same name is still copied) aren't copied into the
temporary `sidecar/`, and `--only` names any index that isn't in the run's sample instead of quietly
skipping it.

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

# Mutation testing, batch 4

The modules that guard written and read data on this PC and in OneLake. They cover:
- MERGE, UPDATE and DELETE (`delta_dml.py`), scored again now that #2528 has changed it.
- The write leases that keep two writers off one table (`leases.py`).
- OneLake file reads, downloads, uploads and listings (`onelake.py`).
- Backing up and restoring the app's folder (`backup.py`).
- A notebook's version history (`notebook_versions.py`) and resources folder (`notebook_resources.py`).
- Finding the data files in a local source (`localdata/discovery.py`).

The run was at `main` f073d852c (2026-11-12), with `sidecar/tools/mutate.py` and the same method, sample cap and seed as batches 1 to 3.

## Results

| Module | Test files run | Mutants (of points) | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `delta_dml.py` | test_delta_dml_edges, test_delta_dml_mutation, test_local_delta_dml, test_merge_conversions | 150 (222) | 146 / 4 | **97.3%** | 148 / 2 | **98.7%** | 2 (batch 2's) |
| `leases.py` | test_leases_edges, test_leases_local, test_table_leases, test_lease_before_commit, test_lease_clock_skew (+ test_leases_mutation) | 150 (223) | 104 / 46 | **69.3%** | 147 / 3 (4 timeouts) | **98.0%** | 3 |
| `onelake.py` | test_onelake_writes, test_contract_onelake, test_paging, test_profile_paths (+ test_onelake_mutation) | 150 (190) | 108 / 42 | **72.0%** | 149 / 1 | **99.3%** | 1 |
| `backup.py` | test_backup, test_backup_edges, test_backup_integrity, test_backup_restore_whole (+ test_backup_mutation) | 150 (209) | 122 / 28 | **81.3%** | 135 / 15 | **90.0%** | 15 |
| `notebook_versions.py` | test_notebook_versions, test_notebook_versions_service_edges (+ test_notebook_versions_mutation) | 130 (130) | 97 / 33 | **74.6%** | 125 / 5 | **96.2%** | 5 |
| `notebook_resources.py` | test_notebook_resources, test_notebook_resources_edges, test_builtin_links (+ test_notebook_resources_mutation) | 120 (120) | 94 / 26 (1 timeout) | **78.3%** | 118 / 2 | **98.3%** | 2 |
| `localdata/discovery.py` | test_local_data, test_local_data_discovery_edges (+ test_localdata_discovery_mutation) | 142 (142) | 113 / 29 | **79.6%** | 136 / 6 | **95.8%** | 6 |

The "after" numbers come from running each module's survivors again with `--only` once its new tests were in.

Test set notes:
- `leases.py`'s set leaves out test_concurrent_writers, which runs several processes at once and takes about 11 s a run.
- `delta_dml.py`'s set leaves out test_spark_merge, which batch 2's set included. That is why it started below batch 2's 98.7%.
- Four of the lease kills are timeouts. Each comes from a lock wait that, once mutated, never gives up: the test would hang, so the mutant is caught.

### What the survivors were, and the tests that now kill them

- **leases.py (43 killed).**
  - Throttling and retries: a throttled OneLake change is sent 3 times in all. A lease file another writer has open gets 50 tries for a change and 10 for a read, and the last try's error is raised. Removing either raise, or moving which try raises, passed every test.
  - The file lock: on POSIX it waits its full timeout before giving up. On Windows, through a stand-in `msvcrt`, it locks and unlocks 1 byte from the start of the file and waits out its timeout. Removing the raise made it wait for ever.
  - The local backend: `delete` says no to a stale etag, and a `replace` whose copy can't be written raises that error rather than a missing-file one.
  - OneLake requests: a 60 s timeout, and a change has three timeouts to finish. A 412 on create is a lost race. A 400 at the create, append, flush, read and release steps is an error; `>= 400` → `>= 401` passed for each.
  - Expiry: an empty lease counts from when its file was written. On the store's clock a lease expires one second after its length, and a lease shorter than a second keeps that length.
  - Defaults: a lease lasts 120 s and a writer waits 120 s. A table lease lasts 60 s and its writer waits 90 s.
  - Waits between tries double up to 15 s.
  - `holder()` names the owner. The renewer is a daemon thread, and `stop` waits 30 s for it.
  - `holding()` is true only inside a live lease on that key.
  - → `tests/test_leases_mutation.py`.
- **onelake.py (41).**
  - A 400 at every kind of request is an error: catalog, list, probe, download, Range read, an upload step, mkdir and delete. `>= 400` → `>= 401` passed at nine places, and a probe answered 400 must say why.
  - Each request's time limit, 60 s or 300 s.
  - A download exactly at its size limit is kept. One a byte over is removed, raises `FileTooBig` and names its limit in MB, and removing that `raise` passed every test. A download also counts from 0, reads 1 MB chunks, streams, makes nested folders and returns its path.
  - A Range read is cut to its length.
  - `list` is shallow by default. Listing the lakehouse root is allowed, and its repeated page is called "Files".
  - The read-only storage options.
  - `TableRef` is frozen and keeps a schema folder unless it is flat.
  - Dates and the catalog's answers.
  - → `tests/test_onelake_mutation.py`.
- **notebook_versions.py (28).**
  - The limits: the 5-minute coalescing window, 30 days to the second, 100 automatic versions, 200 checkpoints and 80-character labels. Saves still keep versions once the checkpoint limit is reached.
  - The refusals for an unknown kind, too many checkpoints and a version no longer kept.
  - Hashing a cell whose source is a list of lines or None, and the code-cell count.
  - After a delete, the index still names the other versions, and no folder is made for a notebook without versions.
  - A coalesced save's index names the new version, not the one it replaced. With that mutant, the index only looked right because it was rebuilt on the next read.
  - Rebuilding a missing or bad index, newest first.
  - `keep_copy`.
  - Renaming into new nested folders, joining existing versions without replacing any, and moving only its own files out of a shared folder.
  - → `tests/test_notebook_versions_mutation.py`.
- **notebook_resources.py (24).**
  - The 120-character name limit and the 50 MB file limit at their edges, with the limit's message.
  - The Windows device names COM1 to COM9 and LPT1 to LPT9, and the names next to them that are fine.
  - Running out of free names after 999.
  - A missing file is refused.
  - What `delete`, `rename_folder`, `copy_folder` and `remove_folder` return. They count nested files too.
  - A copy that fails before making its folder raises its own error.
  - → `tests/test_notebook_resources_mutation.py`.
- **localdata/discovery.py (23).**
  - When two files make one table, which format wins: delta, then parquet, csv, json, then excel. Each pair is set up so that a tie would hand the table to the wrong file.
  - The default depth of 4 folders, `max_depth` from 0 to 10, and 128-character names.
  - Exclude patterns.
  - A dotted file name sets the schema only at the top of a flat source, and only with a table after the dot.
  - The counts and partition flag of single files, Delta and Hive tables.
  - Writer files starting with `_`, and links inside a part folder or below the source (POSIX).
  - Where an issue points: a single file or a folder.
  - An unreadable file's message.
  - The 20,000-file cap.
  - → `tests/test_localdata_discovery_mutation.py`.
- **backup.py (13).**
  - 5 restores' safety copies are kept by default, or the newest 1. Pruning leaves folders it didn't name and files with a restore's name. A missing safety folder prunes nothing.
  - A top-level file named like a skipped folder (`logs`, `engine`) is backed up.
  - An empty `.db` file goes in as a database.
  - A database known only by its header has its stale WAL moved aside.
  - A restore that replaces and moves nothing keeps no safety copy.
  - Backups go into a new nested folder and again into the same one. A backup that stops before its `.part` file exists raises its own error.
  - The restore summary counts replaced files and says whether it restored.
  - → `tests/test_backup_mutation.py`.
- **delta_dml.py (2).**
  - A MERGE with no WHEN clause is refused.
  - A count delta-rs leaves out, or reports as None, is 0.
  - → two tests added to `tests/test_delta_dml_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it can't be caught |
| --- | --- | --- | --- |
| delta_dml.py | 180, 346 | `strict=True` → `False`; back-cast `safe=False` → `True` | As in batch 2. |
| leases.py | 186 | `range(10)` → `range(11)` | The last try raises at attempt 9 either way. |
| leases.py | 348 | `self.clock() if now is None else now` swapped | Every caller of `_body` passes the time. |
| leases.py | 181 | `tmp.unlink(missing_ok=True)` → `False` | The copy was just written, so it exists. |
| onelake.py | 311 | `dest.unlink(missing_ok=True)` → `False` | The download had opened the file for writing, so it exists. |
| backup.py | 310, 339 | the suffix check in `_is_database` swapped; its `return False` → `None` | The header check decides, and only a non-database with a `-wal` beside it would differ; the caller only tests for truth. |
| backup.py | 333 | `no_checkpoint is not None and hasattr(...)` → `or` | Python 3.12 and later have both. |
| backup.py | 133, 149, 219 | JSON `indent` changed | Only the layout of JSON that is read back as JSON changes. |
| backup.py | 295, 397 (×2), 408 | copy buffer `1 << 20` changed | Only the read size changes; 0 means the default. |
| backup.py | 412 | `zip(..., strict=True)` → `False` | Both lists come from the same plan. |
| backup.py | 420 | a companion's `mkdir(parents=True)` → `False` | Step 2 already made the folder. |
| backup.py | 447, 466 | `exc_info=True` → `False` | Only the log line changes. |
| backup.py | 462 | `unlink(missing_ok=True)` → `False` | A file restore swapped in exists. |
| notebook_versions.py | 86 | `_all`'s `return []` → `None` | Its only caller checks the folder first. |
| notebook_versions.py | 91 | `r.get("t", 0)` → `1` | Every record read has `t`. |
| notebook_versions.py | 111 | index `indent=0` → `1` | Layout only. |
| notebook_versions.py | 171, 185 | `unlink(missing_ok=True)` → `False` | The replaced or pruned version is listed, so its file exists. |
| notebook_resources.py | 158, 178 | `unlink(missing_ok=True)` → `False` | The file was made with `open(..., "xb")` just before. |
| localdata/discovery.py | 39 | excel's precedence 4 → 5 | It is last either way. |
| localdata/discovery.py | 90, 91 | `Found.size` and `files` defaults | Every `Found` the module makes sets them. |
| localdata/discovery.py | 124 | suffixes tried shortest first | No two data suffixes both match one name. |
| localdata/discovery.py | 173 | `len(names) >= 1` → `>= 0` | One format found means at least one name. |
| localdata/discovery.py | 265 | a non-table's `hive` → `True` | It is unused when there is no table. |

### Real bugs found

- None. Every killed survivor was a missing test, not wrong behavior.

### Not in this batch

- `audit_chain.py` and `retention.py`, which an open pull request (#2305) still changes.
- The modules other open pull requests change: among them `app_settings.py`, `auth.py`, `meta_service.py`, `service.py`, `server.py` and `fabric_api.py`.
- The policy and approval gates were not touched.

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

# Mutation testing, batch 6

The policy and approval gates that a pull request isn't already changing:
- The settings kept sealed with DPAPI: webhooks and route addresses (`sealed_settings.py`).
- The SQL statement policy for the warehouse (`sql_policy.py`).
- The integrations policy: which integrations may run and who may edit them (`integrations/policy.py`).
- The App Studio model policy: allowed providers and hosts (`appstudio/policy.py`).
- The Git sync gates for Fabric workspaces (`fabric_git_service.py`).
- The warehouse SQL path, including its refusal of change statements and dynamic SQL where they aren't allowed (`warehouse.py`).
- The central policy and its promotion gate (`central/policy.py`).
- The local data service (`localdata_service.py`).

The run was at `main` 7c3dbd0ec (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 5 (sample cap 150, seed 7). `warehouse.py` and `central/policy.py` have more points than the cap, so 150 of each were sampled. Every mutant of the other modules was run.

Owners: the Governance thread owns the policy modules, the Warehouse thread owns Prod writes, and the Data retention thread owns the audit and receipt time limits (not in this batch, see below).

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sealed_settings.py` | test_sealed_settings (+ test_sealed_settings_mutation) | 56 | 52 / 4 | **92.9%** | 56 / 0 | **100%** | 0 |
| `sql_policy.py` | test_sql_exec, test_warehouse_queries, test_tsql_contract | 130 | 130 / 0 | **100%** | 130 / 0 | **100%** | 0 |
| `integrations/policy.py` | test_policy_fail_closed, test_integrations_permissions, test_integrations_executions (+ test_integrations_policy_mutation) | 49 | 45 / 4 | **91.8%** | 49 / 0 | **100%** | 0 |
| `appstudio/policy.py` | test_appstudio_connections, test_appstudio_edit, test_appstudio_generate (+ test_appstudio_policy_mutation) | 60 | 51 / 9 | **85.0%** | 60 / 0 | **100%** | 0 |
| `fabric_git_service.py` | test_fabric_git, test_audit (+ test_fabric_git_service_mutation) | 78 | 63 / 15 | **80.8%** | 78 / 0 | **100%** | 0 |
| `warehouse.py` | test_warehouse_connect, test_warehouse_edges, test_warehouse_precondition, test_warehouse_queries, test_parse_errors_plain, test_safe_parse_readers, test_sql_worker_inprocess (+ test_warehouse_mutation) | 150 of 309 | 124 / 26 | **82.7%** | 150 / 0 | **100%** | 0 |
| `central/policy.py` | test_best_practice_checks, test_central_roles, test_central_upgrades, test_domain_delete_uses, test_file_text_one_line, test_governance, test_platform_retries, test_policy_package, test_policy_promote_gate, test_requests_core_four | 150 of 653 | 150 / 0 | **100%** | 150 / 0 | **100%** | 0 |
| `localdata_service.py` | test_local_data, test_local_data_calls, test_local_delta, test_local_delta_dml, test_local_explain, test_local_transform, test_spill_leftovers, test_sql_exec | 63 | 63 / 0 | **100%** | 63 / 0 | **100%** | 0 |

The "after" numbers re-run each survivor with `--only` and the new tests in.

### What the survivors were, and the tests that now kill them

- **sealed_settings.py**: nothing checked that a value isn't unsealed when DPAPI isn't there, that a webhook set again replaces one that can't be read while an empty one keeps it, that the same holds for route addresses, or that a route without a url passes through unchanged. → `test_sealed_settings_mutation.py`, with the same stand-in DPAPI as `test_sealed_settings.py`.
- **integrations/policy.py**: the answer for a policy file that can't be read was only checked in part, and the editors setting was only checked as true. → `test_integrations_policy_mutation.py` pins the whole fail-closed answer, the defaults, both editors values and a wrong-type value.
- **appstudio/policy.py**:
  - The fail-closed answer was only checked in part.
  - A providers or hosts value that isn't a list.
  - The 40- and 60-character cuts and the limit of 5 names in the problem text.
  - The refusal text with and without an allowed list, and a host matched without regard to case.
  - → `test_appstudio_policy_mutation.py`.
- **fabric_git_service.py**:
  - The item-support answer, for every type and for one.
  - A link file that can't be read, for status and for update.
  - Other Fabric errors passing through status.
  - The text for an update without a Test link.
  - Every gate answer saying it didn't run.
  - Nothing to update.
  - An overview where Fabric refuses the read.
  - → `test_fabric_git_service_mutation.py`, with the fixtures of `test_fabric_git.py`.
- **warehouse.py**:
  - Change statements numbered across GO batches.
  - Names checked inside brackets, and a comment between words keeping them apart (`EXEC/* … */sp_executesql`).
  - OPENROWSET, OPENQUERY and OPENDATASOURCE.
  - The frozen endpoint and the missing-server text.
  - The access-token attribute number and each transient login error number.
  - The retry log text.
  - The name and column limits of a precondition at their edges.
  - A change's row count, commit and close.
  - An empty result.
  - When progress is said.
  - The Arrow types for a one-digit decimal and an all-null datetime column.
  - → `test_warehouse_mutation.py`, with stand-in connections. Progress replaces only `warehouse`'s own `time` name, not the process-wide one.

### Equivalent mutants (left on purpose)

- None.

### Real bugs found

- None. Every killed survivor was a missing test, not wrong behavior.

### Not in this batch

- `app_settings.py` (the policy ceilings and what a setting may loosen), which open pull requests still change.
- `policy_schema.py`, `policy_watch.py`, `policy_trust.py` and `registry_policy.py`, which an open pull request still changes.
- `audit.py` (writing the audit record, and failing closed when it can't), which an open pull request still changes.
- `audit_chain.py` and `retention.py`, which an open pull request still changes.
- `service.py`, which holds the typed-name check for Prod writes and other approval paths, and which several open pull requests still change.
- `lakehouse.py`'s write refusal, already at 100% in batch 2.
- The publish confirmation in `appstudio_service.py`, left for a later batch because the module is large.

# Mutation testing, batch 7

The SQL translation and the notebook's own code, which no open pull request is changing:
- The Spark SQL translation to DuckDB (`sql.py`).
- The notebook files, outputs, history and `%fs` magic (`notebook.py`), leaving out the kernel and the debugger.
- The SQL engines on this PC: DuckDB, SQLite and Polars (`engines/local_sql.py`).
- The local `notebookutils` (`notebookutils_local.py`).

The run was at `main` 7c3dbd0ec (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 6 (sample cap 150, seed 7), on DuckDB 1.5.6 and sqlglot 30.23. Every module has more points than the cap, so 150 of each were sampled. For `notebook.py`, 80 of the 150 fall outside the kernel and debugger (the `Kernel` class and the kernel magics, lines 628 to 2040, and `set_paused`); only those 80 are counted.

`test_notebook.py`, `test_databricks_coverage.py` and `test_sql_exec.py` were not in any run: they read files outside `sidecar/` (such as `examples/data/sales.csv`), which the mutator's copy of `sidecar/` doesn't have, so with them in every mutant is "killed" by a missing file.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sql.py` | test_sql_compat, test_sql_editor, test_sql_run_edges, test_bounded_previews, test_untranslatable_sql, test_tsql_contract, test_shell_and_decimals_edges, test_safe_parse_readers, test_time_travel, test_duckdb_try_cast, test_sql_lambdas and the test_spark_* SQL files (+ test_sql_mutation) | 150 of 894 | 108 / 42 | **72.0%** | 144 / 6 | **96.0%** | 6 |
| `notebook.py` (outside the kernel and debugger) | test_notebook_files, test_notebook_files_edges, test_notebook_folders, test_notebook_shelf_edges, test_ipynb_round_trip, test_output_cap, test_outputs_hardening, test_notebook_display_edges, test_notebook_history, test_notebook_places, test_notebook_resources, test_notebook_inputs, test_notebook_run (+ test_notebook_mutation) | 80 of 150 sampled from 1,680 | 49 / 31 | **61.3%** | 80 / 0 | **100%** | 0 |
| `engines/local_sql.py` | test_engines, test_local_sql_results, test_engine_compare, test_snowflake_sql_variadic, test_untranslatable_sql and the test_spark_* SQL files (+ test_local_sql_mutation) | 150 of 245 | 108 / 42 | **72.0%** | 142 / 8 | **94.7%** | 6, and 2 behind a bug |
| `notebookutils_local.py` | test_notebookutils_fs, test_notebookutils_helpers, test_notebookutils_local_edges, test_notebookutils_parity, test_dbutils, test_keys, test_paging_complete, test_contract_onelake (+ test_notebookutils_local_mutation) | 150 of 449 | 128 / 22 | **85.3%** | 149 / 1 | **99.3%** | 1 |

The "after" numbers re-run each survivor with `--only` and the new tests in. All four new test files also pass on DuckDB 2.0 and on sqlglot 30.22 and 30.27, the ends of the range `pyproject.toml` allows.

### What the survivors were, and the tests that now kill them

- **sql.py**:
  - DuckDB SQL that Spark can't read being read as DuckDB, a key and value call with an odd number of arguments never being read that way, and a lone `X '0A'` read as binary while `t.x '0A'` is left alone.
  - Counting a call's arguments: brackets, square brackets and braces, an unclosed call, a call inside another, and a `datediff` that sits inside brackets.
  - An empty `struct< >` named whichever way it is spelled, including one at the very end of the query and one sqlglot reads without an error.
  - Which name the `current_database()` and `current_schema()` refusals give.
  - Where Spark puts NULL for `array_sort` and `sort_array`, a whole number in brackets, `schema_of_json` for equal types and for a number one past BIGINT, `from_json` as a whole expression, and `str_to_map`.
  - `timestampdiff` in minutes, hours, months, quarters and years, run on DuckDB.
  - `substring` with a start of 0, 1, 2 and -3, as a number and as a column.
  - The Spark name of a rewritten scalar subquery.
  - `bind_tables` binding a table unless it is told the name is local, an older version of a table being superseded, a Databricks `@` time point, and `point_args`.
  - → `test_sql_mutation.py`.
- **notebook.py**:
  - An error at and just past the character cap, and the preview row count for a LazyFrame and for pandas.
  - Clearing outputs with and without waiting, what a run's snapshot holds, and the query progress it reads.
  - The output router's return value, and the `%fs` magic: ls, cp with and without `-r`, a `/mnt` path refused, and put.
  - Value sizes and memory, the watch cap, and durations in s, ms, µs and ns.
  - Notebook folders down to the depth limit, the store's list, cell metadata merging, a save keeping compute and workspace, what delete says about resources, and a shelf move whose copy vanished.
  - → `test_notebook_mutation.py`.
- **engines/local_sql.py**:
  - The 256 MB limit in the descriptor and in the error text, and `Done in` for a statement with no rows.
  - A second cell refused at once while one runs, and the seconds an error cell reports.
  - The row and byte limits in `_drain` and `_keep` at their edges, and the preview across several batches.
  - The DuckDB connection's external access and extension settings being off.
  - Which parser reads Snowflake and Databricks SQL, Databricks' `timediff`, a runnable Snowflake cell, and the Snowflake plan's label.
  - An INSERT's count, the DuckDB 2.0 relation path forced on 1.x, the catalog's view and table types, SQLite's and Polars' no-row results, a one-column Polars result, and the Polars adapter.
  - → `test_local_sql_mutation.py`.
- **notebookutils_local.py**:
  - Each local change returning True, a plain local path, `head`'s default length, and the default `display`.
  - `ls` of a file when OneLake answers 400, `cp` of `Files` with recurse, and `mv` keeping its source when a file didn't land, both on OneLake and locally.
  - The default workspace, `runMultiple` clearing task values only at the top level, `validateDAG`, the UDF function list, and the variable library note printed once.
  - → `test_notebookutils_local_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `sql.py` | 434 | the comparator check negated | Only reached on sqlglot 30.24 and later; on 30.23, where the run was, `array_sort` takes the older branch. The new test kills it on 30.27. |
| `sql.py` | 673, 675 | depth 0 → 1 for `from_json` | The depth only numbers the lambda names, which stay distinct. |
| `sql.py` | 699 | `and` → `or` when putting arguments into a template | The only unqualified columns in the templates are the `fj_arg` ones. |
| `sql.py` | 1047 | `copy=False` → `True` | Copies a node that was already copied. |
| `sql.py` | 1460 | `wrap` default True → False | DuckDB's `LAMBDA x : …` form always writes without brackets. |
| `engines/local_sql.py` | 93 | `_cancelled` starts True | `execute` sets it to False before anything reads it. |
| `engines/local_sql.py` | 358 | `or` → `and` choosing the Arrow reader | Both readers give the same batches. |
| `engines/local_sql.py` | 437, 439 | `strict=True` → `False` in two `zip`s | SQLite's rows and names always have the same length. |
| `engines/local_sql.py` | 481, 488 | `result_rows > 0` → `> 1` in SQLite's batch loop | `_keep` checks the same row and byte limits again on the table the loop builds, so only how early it stops changes. |
| `notebookutils_local.py` | 236 | the size of the `Files` or `Tables` root entry 0 → 1 | Nothing reads that size: moving `Files` itself is refused. |

### Real bugs found

- #2549: on DuckDB 1.x, a DuckDB engine cell with no statement in it, such as only a comment, fails with an internal `AttributeError`, where DuckDB 2.0 says it is done. The two `local_sql.py` survivors on line 357 are the branch that should answer it; on 1.x no input reaches it. They are left for the fix.

### Not in this batch

- `spark_compat.py`: 112 of its 150 sampled mutants survived its own tests, too many to handle alongside the others. It is left for its own batch.
- `spark_local.py`, left with `spark_compat.py` for a later batch.
- The notebook kernel and debugger, as asked.
- `app_settings.py`, `policy_schema.py`, `audit.py`, `service.py`, `audit_chain.py` and `retention.py`, which open pull requests still change.

# Mutation testing, batch 8

The local PySpark and Spark SQL layer, which no open pull request is changing:
- The PySpark calls this PC adds on top of DuckDB's Spark API (`spark_compat.py`), left over from batch 7.
- The local `spark` session: readers, writers, catalog, createDataFrame, query progress and profiles (`spark_local.py`).
- The Spark SQL built-ins written as DuckDB SQL (`spark_builtins_sql.py`).

The run was at `main` 6e76117fe (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 7 (sample cap 150, seed 7), on DuckDB 1.5.6. Every module has more points than the cap, so 150 of each were sampled.

Tests left out of every run:
- `test_spark_read_options.py::test_the_docs_page_is_up_to_date` and `test_spark_api.py::test_the_coverage_page_matches_the_api` read pages under `docs/`. The mutator's copy of `sidecar/` doesn't have that folder, so with them in, every mutant is "killed" by a missing file. A first `spark_local.py` run that had the docs-page test in scored 150 of 150 that way, and it was thrown away. As in batch 7, `test_notebook.py`, `test_databricks_coverage.py` and `test_sql_exec.py` were not used.
- `test_spark_compat.py::test_these_frames_leave_no_view_behind` was deselected. It takes 41 s alone, and every mutant would have paid that.

The "before" runs used each module's own test files. The survivors were then run again three ways:
- With the related `test_spark_*` files that the first set left out, listed in brackets as "existing".
- With the new tests.
- With both together.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_compat.py` | test_spark_compat, test_spark_compat_edges, test_spark_local, test_spark_builtins_sql, test_spark_bad_dates, test_spark_dataframe_decimals, test_spark_integer_casts, test_spark_join_drop, test_spark_select_star, test_safe_parse_readers, test_untranslatable_sql (+ existing test_spark_show, test_spark_column_methods, test_spark_column_names, test_spark_cube_rollup, test_spark_format_string, test_spark_missing_functions, test_spark_nan_rows, test_spark_summary_schema, test_spark_function_names, test_spark_sql_names, test_spark_stat_methods, test_spark_struct_fields, test_spark_divide, test_spark_df_extras, test_pyspark_sql_names, test_spark_cast_names; + test_spark_compat_mutation) | 150 of 1,214 | 88 / 62 | **58.7%** | 141 / 9 | **94.0%** | 7, and 2 left |
| `spark_local.py` | test_spark_local, test_spark_local_edges, test_spark_local_more_edges, test_spark_merge, test_spark_read_options, test_spark_refusals, test_spark_write_types, test_append_conversions, test_spark_compat, test_spark_compat_edges, test_duckdb_try_cast (+ existing test_spark_writer, test_spark_catalog_names, test_spark_csv_options, test_spark_json_parquet_options, test_spark_json; + test_spark_local_mutation) | 150 of 1,025 | 86 / 64 | **57.3%** | 138 / 12 | **92.0%** | 4, and 8 not reached |
| `spark_builtins_sql.py` | test_spark_builtins_sql, test_spark_builtins_sql_edges, test_spark_session_functions (+ test_spark_builtins_sql_mutation) | 150 of 591 | 113 / 37 | **75.3%** | 140 / 10 | **93.3%** | 8, and 2 not reached |

How the survivors were killed:
- `spark_compat.py`: 18 of the 62 by the existing files and 35 by the new tests.
- `spark_local.py`: 29 of the 64 by the existing files and 23 by the new tests.
- `spark_builtins_sql.py`: the existing `test_spark_hash`, `test_spark_hash_refused`, `test_spark_higher_order`, `test_spark_format_string`, `test_spark_views_text`, `test_spark_sql_names` and `test_spark_timestamp_casts` killed none of the 10 left after the new tests.

All three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **spark_compat.py**:
  - `bool_or` and `some`, `bin` in 64-bit two's complement, and `split` with a limit, for a pattern and for characters.
  - `format_string` and `printf`, including a NULL as `null` and a column refused as a format.
  - Seeded `rand`, which repeats for a seed and stays in [0, 1).
  - `first` and `last` with and without `ignorenulls`.
  - `log` with and without a base.
  - The functions that read a number argument as text (`upper(14)` is `'14'`), each one.
  - The `percentile_approx` note, printed only for an accuracy it ignores.
  - `grouping()` refused under a plain `groupBy`.
  - `F.expr` names.
  - `Window.rangeBetween` called on the class, and Window's bounds as PySpark's.
  - `Column.substr` with ints, with Columns, and refused when mixed.
  - The bitwise methods and their names.
  - `between` with infinite and NaN bounds.
  - `getField`, and the names of a struct field and a map key.
  - A map, a struct and an interval in `show()`.
  - A grouped `avg` and `mean`, and dict `agg` counting a column or rows.
  - Rows for structs inside an array.
  - The function list.
  - `install()` run twice.
  - A summary percentile, `drop()` given a number, and `select("`a.b`.*")`.
  - → `test_spark_compat_mutation.py`.
- **spark_local.py**:
  - createDataFrame of single values, of an Arrow table, and refused where a typed value is a row.
  - `_nan` only for a float NaN.
  - A CSV `sep` that DuckDB's sniffer wouldn't pick.
  - `mode("ignore")` writing a new file and keeping an old one.
  - The query progress hint between 0 and 100, with no session and with a closed connection.
  - The cell connection watch.
  - DuckDB's JSON profile in its 1.x and 2.0 shapes, and a real profile.
  - A Parquet or JSON read schema matched by name.
  - A FAILFAST message naming a column that has a quote in its name.
  - DDL `decimal` defaults, and a COMMENT with an escaped quote and a comma.
  - A three-part `USE` refused.
  - A refused write option's text.
  - → `test_spark_local_mutation.py`. It sets LOCALAPPDATA to a temp folder, and the progress and connection tests swap module state only through monkeypatch.
- **spark_builtins_sql.py**:
  - `split` with a limit: Spark's empty-pattern rule (`split('abc', '', 1)` is `['a']`), and a limit from the data refused.
  - `replace` with no replacement.
  - `format_string`:
    - Two's complement by type size for `%x` and `%o`.
    - Explicit argument indexes, and too few arguments.
    - Each refusal message.
    - A format taken from the data.
  - `typeof(signum(...))`.
  - `stack` of one value and of no rows.
  - `try_to_binary` for each format and for a column.
  - `hash()` and `xxhash64()` of nothing, and `hash(-2147483648)` as Spark's int.
  - `extract` from text.
  - `to_char` with a number as its format.
  - `to_csv`, `map_zip_with` and `extract` calls that Spark refuses still stopping.
  - → `test_spark_builtins_sql_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `spark_compat.py` | 1395 | `return False` → `None` | Its callers read it only as true or false. |
| `spark_compat.py` | 1821 | `GroupedData.avg` returns nothing | `install()` replaces `avg` and `mean` with `_numeric_aggregate` before any call. |
| `spark_compat.py` | 2059 | `and` → `or` for a map in `show()` | A struct value reaches `_pretty` as a tuple or Row, never a dict. |
| `spark_compat.py` | 1092 | a function with no signature returned as None | Every F function has a signature. |
| `spark_compat.py` | 2010 | Float digits searched from 1 place, not 0 | 1 place already gives the shortest text that reads back. |
| `spark_compat.py` | 1313 | an unclosed backtick checked as `end < 1` | `find` starts after the backtick, so `end` is never 0. |
| `spark_compat.py` | 1486 | `strict=True` → `False` in `zip` | The lengths are checked just above, or one list is built to match the other. |
| `spark_local.py` | 356 | MERGE source names counted from 0 | Only the names change, and they stay unique. |
| `spark_local.py` | 1185 | the "Only UPDATE and DELETE" raise removed | An Update or Delete tree always parses to a change, so it can't be reached. |
| `spark_local.py` | 1265 | `changed` starts True | It projects the same columns unchanged. |
| `spark_local.py` | 1317 | `strict=True` → `False` in `zip` | The rows and the values made from them have the same length. |
| `spark_builtins_sql.py` | 1024 | `_uncast` negated | sqlglot writes a column and its identifier the same, and a recast timestamp is the same timestamp. |
| `spark_builtins_sql.py` | 684 | `csc` template arity 1 → 0 | sqlglot writes `csc` for DuckDB by itself, giving the same value. |
| `spark_builtins_sql.py` | 716, 717, 562 | the safe flag of `to_timestamp_ntz` and `try_to_timestamp` | With ANSI off, the only Spark mode here, sqlglot writes text that isn't a timestamp as NULL either way. |
| `spark_builtins_sql.py` | 537 | `timestamp_micros` through the decimal path | Multiplying by 1 gives the same microseconds. |
| `spark_builtins_sql.py` | 405 | `map_zip_with` passes the lambda as a third argument | The template has no `fj_arg2`, so it is never used. |
| `spark_builtins_sql.py` | 641 | `window()` with no arguments skips the NULL filter | Such a call is an error either way. |

Left without a test:
- **Not reached without an attached lakehouse**:
  - `spark_local.py` lines 375, 565, 977, 1099, 1124 and 1493 (×2): the MERGE preview, the partition columns in `listColumns`, rebinding tables after a new attach, `CREATE TABLE` properties, `INSERT INTO` a missing table, and `DeltaTable.forPath`.
  - Their tests need a OneLake fake that these runs didn't load.
- **Not reached by any test**:
  - `spark_builtins_sql.py` line 1160: the hash refusal after another rewrite writes `hash` out as text.
  - `spark_builtins_sql.py` line 621: `window()` columns without a GROUP BY.
- **Left**:
  - `spark_compat.py` line 2292: `install()`'s once-only flag. Running it twice gave the same results in the test.
  - `spark_compat.py` line 1571: the summary percentile divided by 101. No small data set picked a different value.
  - `spark_local.py` line 1242: a schema list with a name that isn't text, which PySpark refuses anyway.

### Real bugs found

- #2554: `extract(field FROM s)` and `date_part('field', s)` on a plain text column fail with a DuckDB binder error. Spark reads the text as a timestamp. A text literal and `CAST(s AS STRING)` work.
- #2555: on DuckDB 1.5.6, `F.lit(float('nan'))` and Column comparisons with a NaN float become NULL. Spark's answer is NaN, true or false. DuckDB 2.0 is right.
- #2557: `spark.read.csv` without `sep` lets DuckDB guess the delimiter, where Spark always uses a comma.

None of them is security-relevant.

### Not in this batch

- The other `spark_*` modules: `spark_sql.py`, `spark_infer.py`, `spark_struct_fields.py`, `spark_df_extras.py` and `spark_api.py`.
- The academy PySpark lab test (`test_academy_pyspark_lab.py`), which is in the academy area and was left out.

# Mutation testing, batch 9

The notebook's file formats and files, and the SQL formatter, which no open pull request is changing:
- Notebook import and export in the Fabric, Databricks and Jupyter formats (`notebook_formats.py`).
- The notebook's Parameters bar and widgets (`notebook_inputs.py`).
- Notebook search, opening a file, Recently deleted and restore, and what was published (`notebook_files.py`).
- `runMultiple`'s DAG on this PC (`notebook_dag.py`).
- Format SQL (`sql_format.py`).

The run was at `main` 3dca3ce5b (2026-11-12), with `sidecar/tools/mutate.py` and the same method as the earlier batches (sample cap 150, seed 7), on DuckDB 1.5.6 and sqlglot 30.23. `notebook_formats.py` and `notebook_files.py` have more points than the cap, so 150 of each were sampled; the other three were run whole. `notebook.py` was in batch 7 and `sql_dialects.py` in batch 3, so they aren't here again.

Before each run, its test files were run unchanged in a copy of `sidecar/` alone, as the mutator runs them, to make sure every one passes there. Six tests read files outside `sidecar/` (the docs or `examples/`), so they were deselected: `test_snowflake_sql::test_the_docs_table_is_the_codes`, `test_side_by_side::test_examples_in_the_repository_are_the_generated_notebooks`, `test_notebook_parity::test_spark_reads_files_and_groups_like_pyspark` and three in `test_tsql_loud_gaps` that read the docs' refused list. With them in, every mutant would be "killed" by a missing file.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_formats.py` | test_notebook_formats, test_notebook_formats_edges, test_notebook_formats_roundtrip, test_notebook_formats_service_edges, test_deep_nesting, test_chart_libraries, test_snowflake_sql, test_ipynb_round_trip (+ test_notebook_formats_mutation) | 150 of 443 | 128 / 22 | **85.3%** | 145 / 5 | **96.7%** | 5 |
| `notebook_inputs.py` | test_notebook_inputs, test_notebook_inputs_edges, test_notebook_publish, test_prove, test_side_by_side; then also test_integrations_executions, test_side_by_side_edges, test_dbutils, test_dbutils_parity (+ test_notebook_inputs_mutation) | 117 of 117 | 108 / 9 | **92.3%** | 117 / 0 | **100%** | 0 |
| `notebook_files.py` | test_notebook_files, test_notebook_files_edges, test_notebook_files_service_edges, test_cloud_service_more_edges, test_deep_nesting, test_notebook_folders, test_notebook_mutation; then also test_notebook_batch_edges, test_notebook_formats, test_notebook_formats_service_edges, test_notebook_resources, test_kernel_sessions (+ test_notebook_files_mutation) | 150 of 225 | 112 / 38 | **74.7%** | 145 / 5 | **96.7%** | 5 |
| `notebook_dag.py` | test_notebook_run, test_notebookutils_local_mutation, test_notebookutils_parity, test_dbutils, test_dbutils_parity, test_notebook_parity, test_compute_check_ast, test_wizard (+ test_notebook_dag_mutation) | 88 of 88 | 71 / 17 | **80.7%** | 87 / 1 | **98.9%** | 1 |
| `sql_format.py` | test_sql_format_edges, test_sql_editor, test_snowflake_sql_variadic, test_tsql_contract, test_tsql_loud_gaps, test_code_format (+ test_sql_format_mutation) | 134 of 134 | 116 / 18 | **86.6%** | 132 / 2 | **98.5%** | 2 |

The "after" numbers re-run each survivor with `--only` and the new tests in. The existing test files added after the first run ("then also") killed 1 of the 24 survivors they were tried on alone (the resources folder a delete reports); the new tests killed the rest. Timeouts count as killed: a few `sql_format.py` mutants make the comment scanner loop forever, such as on a block comment that is never closed. All five new test files also pass on DuckDB 2.0.

### What the survivors were, and the tests that now kill them

- **notebook_formats.py**:
  - The 32 MB limit on a source file, and the .dbc limits: 16 MB for one entry, 128 MB in all (one byte over is refused), both checked from the archive's headers before anything is unpacked, and an entry exactly at the limit still read whole.
  - The unsafe-path message, which names the first 120 characters.
  - A Fabric .py export: a `# DBTITLE` line in Python code written as it is, and a Fabric cell marker in Python code escaped so it reads back as one cell.
  - A Fabric METADATA block giving its language to its own cell, including after a markdown cell.
  - The options note on a cell magic line, whole-notebook notes sorted before cell notes, a table's plain text (header and blank nulls), an empty markdown chunk in a percent script, .dbc commands without a position coming first, and a .dbc notebook named by its document or else its file.
  - → `test_notebook_formats_mutation.py`.
- **notebook_inputs.py**:
  - An annotated assignment (`days: int = 7`) as a parameter.
  - A widget left out when its default, its choices or its name isn't a literal.
  - Which widgets are checked against their choices: dropdown and multiselect only, a multiselect split on commas, a dropdown choice with a comma kept whole, and a value left at its default not checked.
  - → `test_notebook_inputs_mutation.py`.
- **notebook_files.py**:
  - Search: an empty search, at most 20 hits per notebook and 100 notebooks, and the 160-character snippet around a match at the start, middle and end of a long line.
  - A free name past "<name> 2", and its 80-character cut.
  - The 20 MB limit on a file to open, and the summary of an opened notebook (counts, language, which cells use Spark, singular and plural).
  - Keeping what was published in a folder made for it, twice; Fabric's cells with ids compared as a reorder.
  - Recently deleted: only entries older than 30 days purged, an expired entry whose file is gone, newest first, whether a resources folder was kept, a restore that brings back two levels of removed folders, and a restore that finds the name taken after all keeping both the notebook there and the deleted copy.
  - → `test_notebook_files_mutation.py`.
- **notebook_dag.py**:
  - The activities a list or a DAG gives when retries, intervals and cell timeouts are left out, and a non-dict activity.
  - The concurrency note only above one.
  - With a fake clock: each run given the DAG's time left (or no limit without one), an activity or a retry skipped once the time is out and still run with a second left, and the retry interval waited between attempts but not after the last or after a success.
  - → `test_notebook_dag_mutation.py`.
- **sql_format.py**:
  - Finding the `--` comments: after a string or name holding dashes, after doubled quotes, after a block comment (empty, holding dashes or a quote), a comment with no newline or space, nested array indexes, and a string or block comment that is never closed.
  - The row-limit check that stops LIMIT turning into TOP, the empty text for no difference, and a lost comment named by its first 40 characters.
  - → `test_sql_format_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `notebook_formats.py` | 214 | the options start 2 → 3 characters after the magic name | The character after the name is always a space or the line's end, which `strip()` removes either way. |
| `notebook_formats.py` | 443 | `lines[1:]` → `lines[0:]` | The header line comes before any marker, so it is never added to a block. |
| `notebook_formats.py` | 480, 495 | `n` default 0 → 1 | Every caller passes `n`. |
| `notebook_formats.py` | 547 | `m.group(1)` → `m.group(0)` | The heading is only searched for `[markdown]` and `parameters`, which `# %%` doesn't change. |
| `notebook_files.py` | 133, 353, 413, 414 | `unlink(missing_ok=True)` → `False` | Each file was written or read just before. |
| `notebook_files.py` | 333 | the purged entry's `unlink(missing_ok=True)` → `False` | Its JSON file was read just before. |
| `notebook_dag.py` | 72 | `concurrency or 0` → `or 1` | 0 and 1 are both "not above one". |
| `sql_format.py` | 156 | the doubled-quote loop turned off (`end == -1`, `end == -0`) | A doubled quote read as a closing quote and an opening one ends the same string. |

### Real bugs found

None in this batch.

### Not in this batch

- `sql.py` and `notebook_practices.py`, which open pull requests change.
- `notebook_folders.py` and the notebook `*_service.py` wrappers, for a later batch (`notebook_versions.py` and `notebook_resources.py` were in batch 4).
