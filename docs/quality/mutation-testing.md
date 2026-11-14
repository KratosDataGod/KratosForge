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

# Mutation testing, batch 10

The rest of the local PySpark layer that batch 8 left, where no open pull request is changing the module:
- createDataFrame's schema inference (`spark_infer.py`).
- `Column.withField` and `dropFields` (`spark_struct_fields.py`).
- Storage levels, `sameSemantics` / `semanticHash` and `Observation` (`spark_df_extras.py`).
- The session and DataFrame calls this PC adds, and the coverage table behind `docs/notebook-spark-on-this-pc.md` (`spark_api.py`).

`spark_sql_names.py` was left out because an open pull request changes it.

The run was at `main` 523163854 (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 9 (sample cap 150, seed 7), on DuckDB 1.5.6. `spark_infer.py` and `spark_api.py` have more points than the cap, so 150 of each were run; the other two were run whole. Before each run, its tests were run in a copy of `sidecar/` on its own, the way the mutator runs them, so that a test which can't run there doesn't count every mutant as killed.

Tests left out of every run, because they read pages under `docs/`, which the mutator's copy of `sidecar/` doesn't have:
- `test_spark_api.py::test_the_coverage_page_matches_the_api`.
- `test_spark_api_coverage.py`: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.
- `test_spark_coverage.py`: the same three tests.

Without them, nothing checked the coverage table itself. `test_spark_api_mutation.py` now checks it without reading `docs/`.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_infer.py` | test_spark_infer_edges, test_spark_infer_schema (+ test_spark_infer_mutation) | 150 of 217 | 123 / 27 | **82.0%** | 147 / 3 | **98.0%** | 3 equivalent |
| `spark_struct_fields.py` | test_spark_struct_fields, test_spark_struct_fields_edges (+ test_spark_struct_fields_mutation) | 129 of 129 | 111 / 18 | **86.0%** | 118 / 11 | **91.5%** | 10 equivalent, 1 killed on DuckDB 2.0 only |
| `spark_df_extras.py` | test_spark_df_extras, test_spark_df_extras_edges (+ test_spark_df_extras_mutation) | 122 of 122 | 118 / 4 | **96.7%** | 120 / 2 | **98.4%** | 1 equivalent, 1 left |
| `spark_api.py` | test_spark_api, test_spark_api_coverage, test_spark_api_parity, test_spark_coverage, test_spark_local (+ existing test_spark_stat_methods, test_spark_views_text, test_spark_writer, test_spark_refusals; + test_spark_api_mutation) | 150 of 258 | 56 / 94 | **37.3%** | 143 / 7 | **95.3%** | 6 equivalent, 1 dead code |

How the survivors were killed:
- `spark_api.py`: 57 of the 94 by existing files that the first set left out (`test_spark_stat_methods` for crosstab, colRegex, toJSON and sampleBy, and `test_spark_views_text` for temp views), and 30 by the new tests.
- The other three modules: all by the new tests.

All four new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **spark_infer.py**:
  - Every inferred field, array element and map value marked nullable, also after two structs merge.
  - bigint's edges: `2**63 - 1` and `-2**63` infer as long; one past either goes to DuckDB's own inference, also after an earlier row of the same shape.
  - A `datetime` going to DuckDB's own inference rather than to `date`.
  - Rows skipped as "the same shape" only when they infer the same: lists or maps of different element types, and a map whose values have no cheap shape.
  - A map with only NULL values refused as `CANNOT_DETERMINE_TYPE`.
  - Spark's decimal(38,18) applied inside arrays and maps (NaN as NULL, 18 places), and flat tuple rows passed on as they are.
  - The `LENGTH_SHOULD_BE_THE_SAME` error naming the right rows.
  - → `test_spark_infer_mutation.py`.
- **spark_struct_fields.py**:
  - `withField` names and values for a dotted path (`col("s.c")`), a field name with a double quote, a NULL value, an aliased struct, a nested edit, two edits in a row, an edit whose value is itself an edit, and `withColumn`.
  - → `test_spark_struct_fields_mutation.py`.
- **spark_df_extras.py**:
  - `semanticHash` a signed 32-bit int for plans on both sides of 2**31.
  - `install()` run twice keeping one `cache` wrapper.
  - → `test_spark_df_extras_mutation.py`.
- **spark_api.py**:
  - The coverage table: the state and reason of methods in each state, the counts in each heading, and each label.
  - `_not_implemented` for a short stub that raises DuckDB's ContributionsAcceptedError, and not for a long body, a body that only names the error, a builtin, or nothing.
  - `sparkContext.defaultParallelism` (the PC's cores, or 1) and `startTime` in milliseconds.
  - `tableExists(name, "global_temp")` in any case, and not under another database.
  - The refusals: crosstab past 10000 column values, a colRegex Python can't read, `toJSON().first()` on no rows, and sampleBy fractions that aren't a dict.
  - toJSON's NaN and infinities, and a timestamp with a zone written in UTC.
  - → `test_spark_api_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `spark_infer.py` | 173 | `_fit`'s last `return value` dropped | `_has_struct` is true only for the four types handled above it, so it can't be reached. |
| `spark_infer.py` | 141 | `_SCALE` from `Decimal(0)` | `quantize` uses only the exponent, which is the same. |
| `spark_infer.py` | 258 | `strict=True` → `False` in `arrow_table` | Rows of different lengths still fail the second, strict `zip` with the schema's fields. `infer` checks lengths first anyway. |
| `spark_struct_fields.py` | 31, 36, 42, 48, 53 | `frozen=True` → `False` on the spec dataclasses | Nothing changes or hashes a spec after it is made. |
| `spark_struct_fields.py` | 107 | `and` → `or` before the dotted-path rewrite | In every case tried (`col("s.c")`, `df.s`, `col("s")["c"]`, an alias), the name came out the same either way. |
| `spark_struct_fields.py` | 174 (×2) | `is_struct`'s `False` → `True` or `None` | Only reached when the frame can't read the column; the select then fails with DuckDB's same binder error. |
| `spark_struct_fields.py` | 194 | `self.sql(inner)` dropped | `_spec` returns the edit itself whenever a column has one, so a `_Col` never holds one. |
| `spark_struct_fields.py` | 198 | `or` → `and` before casting a constant | It casts a constant to the type it already has. |
| `spark_df_extras.py` | 112 | `unpersist(blocking=True)` default | `blocking` is never read: there is nothing to wait for. |
| `spark_api.py` | 264 | `toLocalIterator(prefetchPartitions=True)` default | It is never read. |
| `spark_api.py` | 302 (×2) | the unseeded `randomSplit` salt from a different clock reading | Any salt is a valid random split. |
| `spark_api.py` | 306 (×2) | the last part's upper bound computed instead of 1.0 | A row's point is at most 0.999999, and the weights' sum is 1.0 within float error, so no row is lost. |
| `spark_api.py` | 324 | crosstab reads one more value than 10001 | One past the limit is already enough to refuse. |

Left without a test:
- `spark_struct_fields.py` line 108, `m.group(1)` → `m.group(0)`: only DuckDB 2.0 prints `col("s.c")` as `"s"."c"`, which reaches that line. `test_spark_struct_fields_mutation.py` kills it there (checked by hand), but the mutation runs use DuckDB 1.5.6.
- `spark_df_extras.py` line 187, `Observation.get` with `asDict(recursive=True)`: it changes only a struct metric, from a Row to a dict. No Spark 3.5.3 output for that was at hand to test against.
- `spark_api.py` line 194, `_duckdb_expr`: nothing in the engine calls it, so dropping its result changes nothing. It can be removed in a separate change.

### Real bugs found

None.

### Not in this batch

- `spark_sql_names.py`, which an open pull request changes.

# Mutation testing, batch 12

Four larger modules no batch had covered yet, where no open pull request is changing the module:
- The local pipeline runner (`orchestration/runner.py`).
- A metadata project's data mesh files: products, links, the save gate's mesh rules and the promote rule (`meta/mesh.py`).
- Reading and writing TMDL on this PC (`tmdl.py`).
- Schedules on this PC and Fabric's schedules in one list (`schedule_service.py`).

`orchestration/evaluate.py` was left out because an open pull request changes it. `progress.py`, the next candidate, was left out too: it is the developer profile behind ranks and achievements, next to the academy area this program skips. `schedule_service.py` took its place.

The run was at `main` 4d7f4b6bd (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 10 (sample cap 150, seed 7), on DuckDB 1.5.6. Every module has more points than the cap, so 150 of each were run. Before each run, its tests were run in a copy of `sidecar/` on its own, the way the mutator runs them.

Tests left out of every run:
- `test_pipeline_coverage.py::test_the_page_is_up_to_date` and `::test_the_page_is_in_the_docs_site`, which read `docs/`.
- For `runner.py`, the per-mutant set was kept to the 16 files that run in about 40 seconds, under the mutator's 120-second limit. The rest of the pipeline tests (`test_pipeline_notebooks`, the expression, date, encoding and time zone files, `test_pipeline_coverage`) were then run against the survivors, and killed one.
- For `meta/mesh.py`, `test_platform.py` (about 50 seconds on its own) was likewise run only against the survivors, with the other mesh files below.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/runner.py` | test_pipeline_runs, _script, _data, _schedules, _service_edges, _definitions, _gotchas, _data_edges, _runner_edges, _notebooks_edges, _script_edges, _fabric_parity, _adapters_edges, test_orchestration_definition_edges, _runs_edges, test_cli_tenant_commands (+ existing test_pipeline_notebooks and the expression files; + test_orchestration_runner_mutation) | 150 of 411 | 124 / 26 (9 timeouts) | **82.7%** | 142 / 8 | **94.7%** | 8 equivalent |
| `meta/mesh.py` | test_meta_mesh_edges, test_breaking_changes, test_change_window, test_versioned_contracts, test_product_promises, test_product_status, test_meta_atomic_save (+ existing test_platform, test_mesh_home, test_platform_service_paths, test_schema_evolution, test_git_scopes; + test_meta_mesh_mutation) | 150 of 413 | 127 / 23 | **84.7%** | 148 / 2 | **98.7%** | 2 equivalent |
| `tmdl.py` | test_tmdl, test_tmdl_edges, test_tmdl_write, test_model_ossie_service_edges (+ test_tmdl_mutation) | 150 of 484 | 124 / 26 (2 timeouts) | **82.7%** | 145 / 5 | **96.7%** | 5 equivalent |
| `schedule_service.py` | test_schedules, test_schedule_locks_and_lists, test_schedule_service_edges, test_signed_out_schedules, test_fabric_pipeline_runs, test_fabric_connections (+ existing test_notebook_runs, test_notebook_batch, _edges, _stop, test_pipeline_schedules, test_cli_local_commands, test_integrations_scope_edges, test_integrations_workspace_scope, test_risk; + test_schedule_service_mutation) | 150 of 369 | 83 / 67 | **55.3%** | 150 / 0 | **100%** | none |

How the survivors were killed:
- `schedule_service.py`: 36 of the 67 by existing files the first set left out (a notebook's runs panel, Home's batch runs, pipeline schedules and the CLI), and 31 by the new tests.
- `meta/mesh.py`: 3 of the 23 by existing files (`test_platform` and the others above), 18 by the new tests.
- `orchestration/runner.py`: 1 of the 26 by the rest of the pipeline tests, 17 by the new tests.
- `tmdl.py`: the existing model files (`test_model_date_table`, `_dictionary`, `_directlake`, `test_model_ossie`) killed none; the new tests killed 21.

The runner's 9 timeouts and tmdl's 2 are real hangs: run again with nothing else on the machine, each still passed the 120-second limit, while the runner's set takes about 40 seconds and tmdl's under 5. Examples are a run loop whose wait never ends (`runner.py` 392, 411, 457, 501) and TMDL's file-name loop or reader that never moves on (`tmdl.py` 563, 198).

All four new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **orchestration/runner.py**:
  - A Boolean variable declared without a default starts false.
  - A mock without `seconds` doesn't wait, and parallel work is capped at 32 (and at least 1).
  - Activity ids come from the run id and the activity's path.
  - A skipped activity has no start time; the record's counts add up by state; seconds keep three decimals.
  - The note that Lookup, Get Metadata and Script steps read OneLake appears only when one ran here.
  - A container (If Condition) isn't retried as a whole; a policy timeout counts from the activity's start.
  - A failed mock keeps the output it was given, and one without has none.
  - A notebook step's own failure is its error, not a LocalAdapterError.
  - A run cancelled from outside says `cancelled` and starts nothing, not even a mock that would end at once.
  - A pipeline that runs itself is refused as too deep at check time.
  - AppendVariable keeps a running size of the array.
  - → `test_orchestration_runner_mutation.py`.
- **meta/mesh.py**:
  - The project file's `format = 2`; a link file's `[reads]` and only real pinned environments.
  - A long name that doesn't match its file is cut to 80 characters in the error.
  - A link's summary and pinned tables; a product with no version is version 1.
  - Landing readers grouped by source; the starter catalog read only from a table; a column's saved classification winning over the starter one.
  - Known workspace designations pass and unknown ones don't; a catalog without a classification and a link that exists pass.
  - A promise of 0 % rejected rows is allowed, and a promise is listed once per product for each entity.
  - A status with more than two reasons says "and N more".
  - A deprecation date with a time is read by its date, and the advice to widen accepts keeps the reader's lowest version.
  - → `test_meta_mesh_mutation.py`.
- **tmdl.py**:
  - The exact TMDL the writer lays out: a fenced description with edge spaces, a calculated column without its type, a column of another type with an expression, a format string definition and its own properties, a partition's source expression, compact JSON for an extended property, top-level relationships with `Table.Column` ends, an M expression without its default kind.
  - A reader's note (cultures) reaching the report `from_bim` returns; a bad property name cut to 80 characters; the 20 MB limit on writing checked before reading back.
  - Files read as database, model, expressions, then tables, then relationships, then the rest.
  - A relationship with one end missing still reads; a format string definition ends where its siblings begin.
  - `column_ref`'s 1,024-character limit and its bad forms; `unquote` and values need both quotes.
  - → `test_tmdl_mutation.py`.
- **schedule_service.py**:
  - What each call returns: a deleted schedule's id, Fabric's answer to a change, an empty text when no alert needs recording (no webhook, a run that worked, an alert that went out).
  - A changed schedule passes `signed_out` only when given; 200 runs in the history and 50 in a notebook's panel unless asked.
  - A one-cell notebook says "1 cell ran".
  - A pipeline schedule checks and starts with empty settings left out (None, not `{}` or ""), refuses a pipeline that can't run here or can't be read, and names one SecureString as one.
  - A scheduled pipeline run followed to its end, and a failed one naming its failed activity.
  - Only signed-out schedules skipped when IT turned them off, with 0 seconds.
  - Another kind of schedule with the notebook's name isn't the notebook's; Fabric run seconds keep one decimal; both Fabric reads ask for 500 runs.
  - A lock older than 12 hours taken over even from a live process; the alerts lock left by a dead run taken.
  - A stopped run's pid kept only when it is a whole number; checking a process sends it no signal.
  - An ended process is `False`, pid 1 with another start time has ended, and Windows' process times ask for all four times and read the last error.
  - Run now on Fabric uses Data Factory only for its item types; a notebook's publish workspace listed and one without none; a Fabric schedule without `enabled` is off.
  - → `test_schedule_service_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `runner.py` | 561 | `_matches`' last `return False` → `None` | Its callers only test it for truth. |
| `runner.py` | 660 | `or` → `and` in ForEach's check before starting an iteration | An iteration started after a cancel raises in `run_list` before it records anything, so nothing seen changes. |
| `runner.py` | 710 | a child run's `depth + 1` → `depth - 1` | Nesting is refused at check time by the top-level run, and depth is otherwise only compared with 0, which a child never is either way. |
| `runner.py` | 749 | `_watch_child`'s time left computed with `+` | The wait is capped at 0.2 seconds either way, so the deadline check keeps the same pace. |
| `runner.py` | 450 | `getattr(adapter, "container", False)` → `True` | Every adapter in the registry sets `container`, so the default is never used. |
| `runner.py` | 408, 664 | `daemon=True` → `False` on activity and ForEach threads | Each is joined before the run ends; the flag only matters at interpreter exit. |
| `runner.py` | 768 | `child.id[:8]` → `[:9]` in a thread's name | Only the thread's name changes. |
| `mesh.py` | 172 | `_incident_touches`' `return False` → `None` | Its callers only test it for truth. |
| `mesh.py` | 323 | `identity`'s `format` default 1 → 0 | `0 or 1` is still 1. |
| `tmdl.py` | 122 | the `if not dot: raise` removed in `column_ref` | The column part is then empty, and the next check raises the same error. |
| `tmdl.py` | 342, 345 | `_file_order`'s ranks 10 → 11 and 30 → 31 | The order of the files is the same. |
| `tmdl.py` | 169 | a fenced expression's `default=0` → `1` | Only used when the body has no line with text, and then every line is blank either way. |
| `tmdl.py` | 246 | `m.group(1)` → `m.group(0)` for `^(\w+)$` | The whole match is the group. |

### Real bugs found

None.

### Not in this batch

- `orchestration/evaluate.py`, which an open pull request changes.
- `progress.py`, the developer profile, next to the academy area.

# Mutation testing, batch 11

Three modules no open pull request is changing:
- The Snowflake SQL translator that runs Snowflake cells on this PC (`snowflake_sql.py`).
- The SQL compatibility rules behind the SQL editor's and the notebook's compatibility check (`sqlcompat/rules.py`).
- The ipywidgets stand-in that notebook cells import (`widgets.py`).

The run was at `main` 4d7f4b6bd (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 10 (sample cap 150, seed 7), on DuckDB 1.5.6. All three modules have more points than the cap, so 150 of each were run.

Before each run, its tests were run in a copy of `sidecar/` on its own, the way the mutator runs them. This batch also ran them once against the module as `ast.unparse` writes it, because that is the text every mutant is written as. That check found a false 100% for `snowflake_sql.py`: `test_snowflake_sql.py::test_every_rule_the_code_uses_is_in_the_docs_table` reads the module's source for rule ids in double quotes, and `ast.unparse` writes single quotes, so the test failed for every mutant and each counted as killed. That run was thrown away, and the test is left out of the runs below.

Tests left out of every run, because they read pages under `docs/` or the module's own source:
- `test_snowflake_sql.py`: `test_the_docs_table_is_the_codes` and `test_every_rule_the_code_uses_is_in_the_docs_table`.
- `test_snowflake_sql_parity.py::test_the_docs_carry_the_table_and_say_no_account_was_used`.
- `test_snowflake_coverage.py`: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.
- `test_learn_guides.py::test_the_shipped_guides_are_the_docs`.
- `test_practice_docs.py`: `test_every_rule_has_a_docs_section` and `test_no_docs_section_for_a_rule_that_doesnt_exist`.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `snowflake_sql.py` | test_local_sql_mutation, test_snowflake_coverage, test_snowflake_sql, test_snowflake_sql_edges, test_snowflake_sql_parity, test_snowflake_sql_variadic, test_sql_lambdas (+ test_snowflake_sql_mutation) | 150 of 732 | 132 / 18 | **88.0%** | 144 / 6 | **96.0%** | 6 equivalent |
| `sqlcompat/rules.py` | test_learn_fixes, test_learn_fixes_edges, test_learn_guides, test_localdata_engine_mutation, test_practice_docs, test_snowflake_sql_variadic, test_sql_compat, test_sql_practices, test_tsql_contract (+ test_sqlcompat_rules_mutation) | 150 of 1133 | 102 / 48 | **68.0%** | 137 / 13 | **91.3%** | 10 equivalent, 3 tied to #2623 and #2624 |
| `widgets.py` | test_ipywidgets, test_ipywidgets_parity, test_output_cap, test_outputs_hardening, test_widgets_more_edges (+ test_widgets_mutation) | 150 of 519 | 127 / 23 | **84.7%** | 146 / 4 | **97.3%** | 4 equivalent |

Existing test files the first sets left out were tried on the survivors first, and they killed none:
- `snowflake_sql.py`: test_snowflake_coverage_edges, test_local_sql_errors, test_local_sql_results, test_sql_portability.
- `sqlcompat/rules.py`: test_learn_labs, test_opportunities, test_risk, test_sql_portability.
- `widgets.py`: test_chart_libraries, test_dbutils, test_dbutils_parity, test_display_handles, test_notebook_inputs, test_notebook_inputs_edges, test_notebook_inputs_mutation, test_notebook_mutation, test_notebook_formats.

All the survivors that were killed, were killed by the new tests. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **snowflake_sql.py**:
  - A divisor written as text (`10 / '0'`) or as a column named like a number (`"2"`) still gets Snowflake's "Division by zero".
  - `OCTET_LENGTH` of BINARY counts bytes (`octet_length`), not the characters of its text.
  - `OBJECT_INSERT` with a `FALSE` update flag refuses a key that is already there.
  - `FLATTEN` with a positional input, and `OUTER => FALSE` against `OUTER => TRUE` on an empty array.
  - `ALTER ... SET TAG` refused as a Snowflake setting, while `ALTER TABLE ... RENAME` runs.
  - `AS_DECIMAL` refusing a precision of 39, and the reason each `AS_*` function gives when it has too many arguments.
  - The translation label carrying each note in full, and the report objects being frozen.
  - → `test_snowflake_sql_mutation.py`.
- **sqlcompat/rules.py**:
  - The exact text each finding points at, for 39 statements across the rules: types, `CREATE`/`ALTER` objects, constraints, hints, the endpoint's read-only writes, the clock words, date patterns, `ISNULL`, `GO` and CTEs.
  - `#temp` names: `#t` is a temp table and `# t` isn't, and `##g` is global.
  - Columns added by `ALTER TABLE`, with and without `COLUMN`, and after a comma.
  - A table made from a query, past `WITH (options)` and inside `CONVERT`.
  - An `UPDATE` whose `WHERE` comes after a subquery still has one.
  - A CTE with a column list that names itself, and the second of two CTEs.
  - Statements that end early (an unclosed column list, a lone `WITH`), read to their end without failing.
  - More than 1,024 columns, a bad name quoted to 64 characters, and the rules being frozen.
  - → `test_sqlcompat_rules_mutation.py`.
- **widgets.py**:
  - How deep a nested view is drawn: down to `MAX_DEPTH`, then a placeholder.
  - The controls `interact` picks: the range of an int or float slider around its value and the float step in hundredths, a two- or three-number tuple as a slider with a step of 1, and anything else listed as a dropdown.
  - A range slider starting at a quarter and three quarters of its bounds.
  - `Text` turning `None` into an empty value, and a dropdown's description being settable.
  - `Output` without and with a kernel bound: `with out as o` gives the widget, an error inside isn't swallowed, `capture()` keeps what was there, and an output that exactly fills the rich cap is shown, also after `clear_output()`.
  - `interact` as a decorator and through `interact.options()` giving back the function.
  - Installing twice keeping this module.
  - → `test_widgets_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `snowflake_sql.py` | 1363 (×2), 1376 | `_div0_branch` misreading DIV0's or DIV0NULL's division | A division it misses gets the zero check too, inside the `ELSE` branch DIV0 never reaches when the divisor is 0. DuckDB's `CASE` doesn't evaluate a branch it doesn't take, so the results are the same. |
| `snowflake_sql.py` | 1377, 1160 | `return False` → `return None` | Both callers only test the result for truth. |
| `snowflake_sql.py` | 953 | `COALESCE(running, 0)` → `1` for `CONDITIONAL_TRUE_EVENT` | The running sum of 0s and 1s up to the current row is never NULL. |
| `sqlcompat/rules.py` | 366 | `_where_follows`'s `return False` → `None` | Its caller only tests `not _where_follows(...)`. |
| `sqlcompat/rules.py` | 338 | `_args` counting commas from the `(` instead of after it | The `(` isn't a comma. |
| `sqlcompat/rules.py` | 605 | `ISNULL`'s argument count from the name instead of the `(` | A bracket is at its call's own depth, so the same `)` and the same commas are found. |
| `sqlcompat/rules.py` | 646 | `_regex_pattern` looking for commas from before the `(` | The two extra tokens are the name and the `(`, never a comma at the argument depth. |
| `sqlcompat/rules.py` | 764 | `_close(s, k + 1)` → `_close(s, k)` past `WITH (options)` | From `WITH`, the first `)` at the same depth is the same one. |
| `sqlcompat/rules.py` | 908 | the `NOT NULL` search starting two tokens earlier | Those tokens are the table name and `ADD`. |
| `sqlcompat/rules.py` | 977, 978 | the `CREATE`/`ALTER`/`DROP` guard and its look-ahead | The slice is safe without the guard, and only the first three words are read. |
| `sqlcompat/rules.py` | 1048 | the self-reference search starting at `AS` | `AS` and `(` are never a CTE's name. |
| `sqlcompat/rules.py` | 1087 | `FROM (` read at `FROM` | A `(` isn't a name either way, so the token is skipped. |
| `widgets.py` | 153 | `_view(depth=0)` default | Every caller passes the depth. |
| `widgets.py` | 230 | `IntSlider` reading a value from the app as `float` | Setting `value` casts it to `int` anyway. |
| `widgets.py` | 618 | `Literal[False]` → `Literal[True]` in `__exit__`'s annotation | An annotation. `test_widgets_mutation.py` checks that the error isn't swallowed. |
| `widgets.py` | 636 | `capture(wait=True)` default | `clear_output` doesn't read `wait`. |

Left without a test, tied to a bug filed in this batch:
- `sqlcompat/rules.py` lines 294 and 299, `_name_at`: the `db..table` branch can't be reached, so neither mutant changes anything until #2624 is fixed.
- `sqlcompat/rules.py` line 985, `_writes`' `k + 1 < len(toks)` guard for `SELECT ... INTO` at the end: for the T-SQL targets, sqlglot fails on that statement before the rules run (#2623).

### Real bugs found

- #2623: the SQL compatibility check fails with `AttributeError` on a T-SQL `SELECT ... INTO` with no table name, because sqlglot's T-SQL parser throws one and `analyzer.parse()` doesn't catch it.
- #2624: a `db..table` name isn't read past the first dot, so `CREATE TABLE db..t (a nvarchar(10))` gets no findings.
- #2625: `widgets.install()` keeps a real ipywidgets that a library imported first, because only KratosForge's module has `__kratosforge__` and the check defaults to `True`.

None of them is security-relevant.

### Not in this batch

- `snowflake_sql.py`, `sqlcompat/rules.py` and `widgets.py` past the 150 sampled mutants.

# Mutation testing, batch 13

Three modules no open pull request is changing:
- Connection profiles: which platforms a piece of work uses, its sign-in references, export and import, and reading the Databricks and Snowflake CLIs' own files (`connprofiles.py`).
- The result viewer behind a notebook table output: paging, the profile, charts and downloads, with its small Excel writer (`frames.py`).
- The Databricks SQL warehouse engine (`engines/databricks_sql.py`).

The run was at `main` fb729823b (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 12 (sample cap 150, seed 7), on DuckDB 1.5.6. All three modules have more points than the cap, so 150 of each were run.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test passed both ways, so no run below can score a false 100%. One extra test was left out of the `connprofiles.py` re-run because it reads a page under `docs/`: `test_platforms.py::test_the_docs_table_is_the_codes`.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `connprofiles.py` | test_connprofiles, test_connprofiles_edges, test_connprofiles_key_text, test_connprofiles_shape, test_databricks_host, test_databricks_profile_host, test_engine_connections_settings, test_engines_service, test_engines_service_edges, test_secret_detectors_userinfo (+ test_connprofiles_mutation) | 150 of 406 | 137 / 13 | **91.3%** | 150 / 0 | **100%** | none |
| `frames.py` | test_frames, test_frames_more_edges, test_notebook_display_edges, test_outputs_hardening, test_query_profile, test_spark_summary_schema, test_wrangler, test_write_types (+ test_frames_mutation) | 150 of 424 | 126 / 24 | **84.0%** | 149 / 1 | **99.3%** | 1 with no deterministic test |
| `engines/databricks_sql.py` | test_credential_expiry, test_databricks_discovery, test_databricks_sql, test_databricks_sql_errors, test_databricks_sql_robust, test_paging, test_remote_text_lines, test_reply_text_one_line (+ test_databricks_sql_mutation) | 150 of 373 | 133 / 17 | **88.7%** | 150 / 0 | **100%** | none |

The 7 timeouts (2 in `connprofiles.py`, 1 in `frames.py`, 4 in `engines/databricks_sql.py`) are real hangs: each mutant turns a loop's exit or a lock's non-blocking try into one that never ends (`import_`'s new-id loop, `_unique`'s suffix loop, the statement poll, and a second cell waiting on the first).

Existing test files the first sets left out were tried on the survivors first:
- `connprofiles.py`: test_databricks_billing, test_databricks_bundles_service, test_databricks_runs_service, test_databricks_sync_service, test_dbconnect, test_dbconnect_env, test_engine_conformance, test_platforms. They killed none.
- `frames.py`: test_kernel_process_edges, test_notebook_history, test_pandas_in_notebooks, test_publish_mutation, test_service_edges, test_state_writes. They killed none.
- `engines/databricks_sql.py`: test_cli_tenant_commands, test_databricks_billing, test_databricks_jobs, test_databricks_runs_service, test_databricks_sync_service, test_databricks_workspace_sync, test_databricks_workspace_sync_edges, test_dbconnect_env, test_engine_conformance, test_engines_service, test_engines_service_edges. They killed 2: the adapter's `describe()` and a whole result asked for with `result_rows` of 1.

The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **connprofiles.py**:
  - A private key file on a network share (`\\server\...` or `//server/...`) refused with its own reason.
  - Import refusing a secret anywhere in the document, not only inside its profile.
  - Databricks on AWS or GCP refused, since only Azure is turned on.
  - A service principal needing both its client id and its secret reference.
  - The local cache defaulting to 10 GB.
  - Export marking only filled-in credential fields as needing setup.
  - The Databricks CLI file reading as found, and a profile with only a host signing in through the CLI.
  - A CLI profile or connection name making an id of 40 characters.
  - The Snowflake CLI folder following this computer's system.
  - A Snowflake CLI connection warned about only when it uses a sign-in KratosForge can't.
  - Deleting a profile that was never saved.
  - → `test_connprofiles_mutation.py`.
- **frames.py**:
  - A page of 100 rows by default, and frame ids of 12 hex characters.
  - The Arrow table made once and reused.
  - An Excel download of 1,048,576 rows refused before anything is written.
  - The profile covering 200 columns and saying how many more there are, sampling only above 250,000 rows, and drawing 12 histogram bins.
  - The type names for true/false and struct columns, and a NaN chart category keeping its own key.
  - A bar chart keeping the largest categories first, and a line chart keeping x order with a missing x last.
  - Summing a decimal column keeping its fractions, and count counting rows where the value is missing or no value is picked.
  - A series chart with no "Other" slot, and a histogram binning by the values' range.
  - Excel dates around the 1900 leap day that never was, decimals of more than 15 digits going out as text, column letters past Z, text keeping tabs and line breaks, and each row getting its own number.
  - → `test_frames_mutation.py`.
- **engines/databricks_sql.py**:
  - The two-hour limit, counted from when the statement was sent, and its message in minutes.
  - An error cell reporting its own running time.
  - A plain cell and Plan sending no row limit, and a whole result of one row asking for two.
  - A whole result's preview starting at its first row, and a first chunk that says it starts at row 0 being read.
  - A whole result of exactly the size limit kept.
  - A result chunk that isn't an object refused by name.
  - A refused token tried once more with a new one, a new token that is refused too being forgotten, and a sign-in that can't be renewed being a 401 error.
  - Requests waiting a minute at most.
  - Cancel tried again after Cancel was pressed, while other calls aren't.
  - A cell's catalog and schema winning over the connection's.
  - → `test_databricks_sql_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `frames.py` | 457 | `sname in values and _chart_key(k) in index` → `or` | Every series name the chart's last query gives is in `values`, and its x values come from the same ordered list of kept categories. They can only differ when categories tie at the 60-category cut and DuckDB orders the tie differently in two queries, which no test can make happen on demand. |

### Real bugs found

None.

### Not in this batch

- `connprofiles.py`, `frames.py` and `engines/databricks_sql.py` past the 150 sampled mutants.

# Mutation testing, batch 14

Three modules no open pull request is changing:
- Where a metadata project's tables live: reads, writes, appends that lose a race for the next version, MERGE, the run metadata tables and the files a failed write left (`meta/store.py`).
- A metadata project itself: its layers file, exceptions, the save gate, the run order and what is downstream of an entity (`meta/project.py`).
- Run requests from Fabric pipelines, the agent's side of the mailbox in `LH_Meta` (`mailbox.py`).

The run was at `main` 5c80b9a53 (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 13 (sample cap 150, seed 7), on DuckDB 1.5.6. All three modules have more points than the cap, so 150 of each were run. Each mutant's tests ran with `-n 2`, so a set of about a minute fits the 120-second limit.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batches 11 to 13. Every test passed both ways, so no run below can score a false 100%. Eight `mailbox.py` tests fail in a copy of `sidecar/` alone because their `nb` fixture reads `examples/pipeline/run_on_agent.ipynb`, so they are left out of every run: in `test_mailbox.py`, `test_a_pipeline_request_runs_and_reports_back`, `test_the_same_request_runs_once_however_often_it_is_sent_or_read`, `test_a_cancel_file_stops_a_request_before_it_runs`, `test_a_cancel_that_arrives_between_polls_stops_the_worker_before_it_starts`, `test_cancelling_a_running_request_lets_the_run_finish`, `test_the_notebook_gives_up_cancels_and_fails_after_its_timeout` and `test_the_notebook_names_its_pipeline_run_when_given_one`; and `test_agent_queue_bound.py::test_a_waiting_request_is_cancelled_by_its_cancel_file_and_is_never_queued`. `test_meta_atomic_save.py` reads `meta/project.py`'s source, but only to check that it never calls `.write_text(`, which no mutant adds, so it stays in.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/store.py` | test_meta_store_edges, test_meta_compaction, test_meta_filtered_reads, test_meta_write_types, test_durable_commits, test_lease_before_commit, test_delta_profile_writes, test_awkward_paths, test_landing_manifest, test_landing_zones, test_crash_kills, test_bounded_previews, test_conversions_everywhere, test_full_load_memory, test_meta_keys_edges, test_batches (+ test_meta_store_mutation) | 150 of 373 | 117 / 33 | **78.0%** | 144 / 6 | **96.0%** | 6 equivalent |
| `meta/project.py` | test_meta_project_edges, test_meta_project_folder, test_meta_atomic_save, test_meta_compare_edges, test_meta_compare_totals, test_meta_engine_edges, test_meta_source_systems_edges, test_project_version_walk, test_schema_evolution, test_versioned_contracts, test_lineage, test_shared_keys, test_breaking_changes, test_model_date_table, test_sealed_settings, test_platform_service_paths, test_meta_service_edges, test_meta_service_keys_edges, test_meta_keys_edges (+ test_meta_project_mutation) | 150 of 452 | 85 / 65 | **56.7%** | 148 / 2 | **98.7%** | 2 equivalent |
| `mailbox.py` | test_mailbox, test_mailbox_edges, test_mailbox_more_edges, test_agent, test_agent_clock_and_folder, test_agent_queue_bound, test_agent_recovery, test_agent_silent_failures, test_agent_status, test_agent_takeover, test_paging, test_lease_clock_skew, test_shared_schedules, test_schedule_overlap, test_sqlite_connections_closed, test_not_set_up, test_cli_local_commands, test_contract_onelake (+ test_mailbox_mutation) | 150 of 416 | 123 / 27 | **82.0%** | 144 / 6 | **96.0%** | 6 equivalent |

The one timeout, in `meta/project.py`, is a real hang: it was run again on its own and still passed the 120-second limit. Swapping `and` for `or` in `impact`'s test of which entities to add (`f in e.reads() and n not in out and n != entity`) adds the same entities again on every round, so the loop never ends.

Existing test files the first sets left out were tried on the survivors first:
- `meta/store.py`: test_concurrent_writers, test_maintenance_concurrency, test_dataops, test_dataops_edges, test_large_tables, test_keys. They killed 9: the row count from the log's statistics, the append retry setting, and the race retries' conditions and waits. One more timed out there while the machine was loaded; the new tests kill it directly.
- `meta/project.py`: test_meta, test_keys, test_batches. With test_meta at about 40 seconds, the first 9 survivors took a quarter of an hour, so the run was stopped there. They killed 4 of those 9, and the new tests kill all 4 as well.
- `mailbox.py`: none beyond the first set.

The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **meta/store.py**:
  - Where a run ran, in words: Fabric, an agent with or without its host, or this PC.
  - Create-if-absent saying `False` when the table was already there.
  - The schema kept after a write only for the version that write made.
  - A MERGE into an existing table refusing a type that would change values.
  - An upsert on a key that bounds nothing (a float) still merging, and an upsert and a stream keeping their commit metadata.
  - A stream that loses its lease saying so with its own cleanup note, not as a conflict; and a stream into a table another writer made first being a conflict.
  - An append that keeps losing the Windows commit race, or keeps losing to another writer's create, giving up after ten tries with waits of 0.05 s, 0.1 s and so on; an overwrite never sent again; and an append that lost to another writer's create once being made again on the table as it is now.
  - The files a failed stream left counted as "1 data file" or "2 data files", and a stream whose files couldn't be listed first removing none.
  - → `test_meta_store_mutation.py`.
- **meta/project.py**:
  - `max_deletes` taking 100% but not more.
  - The logic hash ignoring the settings that only change how a run reads and the order settings are written in, and changing for one that picks rows.
  - Soft deletes only for an incremental load, and the columns a dimension, a lookup and soft deletes make.
  - The source an entity's summary names, for files, a landing folder, a link and an upstream entity.
  - The layers file keeping the rules and a retention other than the default, and leaving both out when there are none.
  - A layer keeping versions for 7 to 3,650 days, a project having 10 layers but not 11, a layer name in lowercase, and a layer list that breaks a read being refused with the old file kept.
  - A misnamed entity file reported with its name cut to 80 characters, and the environments a project doesn't name getting their defaults.
  - The fingerprint counting the Python an entity runs, and only for an entity that runs some.
  - An exception in force to the end of its last day, waiving its own rule only; and its status and notice by days left: today, 1 day, 14 days, 15 days, ended, permanent, or not a date.
  - The save gate on expired and unreadable exceptions, a link's columns, `batch_by`, `lookback`, deletes and soft deletes, a source path on this PC, `overwrite_schema`, same-layer reads, the layers a layer may read, a not-null check on a non-key column, a custom step, and a custom function that can't be imported.
  - The run order given whatever other problems there are, a cycle found and named from where it closes, and everything downstream of an entity.
  - → `test_meta_project_mutation.py`.
- **mailbox.py**:
  - The defaults: no backfills from pipelines, request files kept for 14 days, and a request given up after 3 agents.
  - A status written as indented JSON.
  - A pipeline run up to 24 hours old, starting up to 5 minutes ahead of this clock, and only a Pipeline job; a lookup Fabric couldn't answer marked to try again, and one it refused not.
  - Pipeline runs already used forgotten after 24 hours and kept until then.
  - A running job's status with no finish time yet, a cancel while running noted only then, and a job change updating only this agent's open requests.
  - A cancel check only for this agent's environments.
  - A request three agents lost failing, a busy agent taking a lost request over unless it claims only while free, and an owner in OneLake judged lost only past its fresh time plus the grace.
  - A finished request's files removed after 14 days and not before.
  - A refused listing of a mailbox folder being an error.
  - → `test_mailbox_mutation.py`.

### Equivalent mutants

| Module | Line | Mutant | Why it changes nothing a test can see |
| --- | --- | --- | --- |
| `meta/store.py` | 338 | `safe != data.schema` → `==` on the retry after another writer's create | The rows are already cast to the safe types before the first try, so on the retry the two are the same and the cast is skipped either way, unless another writer made the table with other types at that same moment, which no test can arrange on demand. |
| `meta/store.py` | 357 | `isinstance(self, LocalStore)` negated before a stream makes the table's folder | delta-rs makes the folder itself, so a stream into a new local table works without it. |
| `meta/store.py` | 395 | `_delta_safe`'s `loaded=False` default → `True` | Only `write_stream` uses the default, for a write that isn't an append, and the table is loaded only for an append. |
| `meta/store.py` | 693 | `maintain_order=True` → `False` in `meta_where`'s sort | Rows are sorted by their write time; no test can make two rows with the same time come back in a different order. |
| `meta/store.py` | 791 | `version + 1` → `version - 1` in the commits flushed after a compaction | The flush then also covers two commits already flushed; nothing a test can see. |
| `meta/store.py` | 803 | the compaction lease's `ttl=120` → `121` | A second more on a lease no test waits out. |
| `meta/project.py` | 389 | `usedforsecurity=False` → `True` | The same SHA-1. |
| `meta/project.py` | 709 | `overwrite_schema`'s default `False` → `True` | The check refuses only a value that isn't true or false, and both defaults are. |
| `mailbox.py` | 156 | the lock holder read from byte 1 → byte 0 | Byte 0 is a space, which `json.loads` skips. |
| `mailbox.py` | 157 | the holder read as 4,096 bytes → 4,097 | A holder record is far smaller. |
| `mailbox.py` | 189 | a folder listing's `timeout=60` → `61` | A second more on a request no test waits out. |
| `mailbox.py` | 218 | a Fabric time's fraction cut to 6 digits → 7 | Python 3.12's `fromisoformat` takes the seventh digit and drops it. |
| `mailbox.py` | 411 | `e.get("at") or 0` → `or 1` | Every entry has a numeric `at`: entries without one are dropped on loading, and new ones always get it. |
| `mailbox.py` | 628 | `return False` → `return None` for a record that isn't an agent's | Both are false to the caller. |

### Real bugs found

None.

### Not in this batch

- `meta/store.py`, `meta/project.py` and `mailbox.py` past the 150 sampled mutants.
- `OneLakeStore` beyond the addresses and file listings, which needs a OneLake to talk to.

# Mutation testing, batch 15

Three modules no open pull request is changing:
- The engine picker behind a notebook: connections, the confirm and risk check a remote engine asks for, switching engines, Databricks sync pairs, and the threads a session and a cell run on (`engines_service.py`).
- OneLake shortcuts: the shortcut center, its history, repointing, finding a moved target, undo, and shortcut requests (`shortcut_service.py`).
- The Estate X-ray service: a scan and its cost findings, Optimize and Vacuum with their risk check, leftover Dataflow Gen2 staging lakehouses, and adopting a lakehouse into the metadata project (`xray_service.py`).

The run was at `main` e0187cd1a (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 13 (sample cap 150, seed 7), on DuckDB 1.5.6. All three modules have more points than the cap, so 150 of each were run. It ran beside batch 14, which took other modules.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test passed both ways, so no run below can score a false 100%. One test file was left out of the `shortcut_service.py` runs because two of its tests read the app's own files under `app/`: `test_rpc_surface.py` (`test_every_rpc_method_has_a_ui_caller_or_a_listed_reason` and `test_the_exception_list_stays_honest`).

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `engines_service.py` | test_databricks_billing, test_databricks_bundles_service, test_databricks_runs_service, test_databricks_sync_service, test_engine_connections_settings, test_engines_service, test_engines_service_edges, test_fabric_spark (+ test_engines_service_mutation) | 150 of 391 | 130 / 20 | **86.7%** | 150 / 0 | **100%** | none |
| `shortcut_service.py` | test_shortcut_center, test_shortcut_fixes, test_shortcut_service_paths, test_state_writes (+ test_fail_closed, test_shortcut_request_envs, test_shortcut_request_retries, test_shortcut_watch, test_typed_errors, test_shortcut_service_mutation) | 150 of 366 | 104 / 46 | **69.3%** | 148 / 2 | **98.7%** | 2 equivalent |
| `xray_service.py` | test_failed_lookups, test_optimize_service, test_paging_complete, test_xray, test_xray_service_edges, test_xray_staging (+ test_audit, test_estate_service_edges, test_optimize_outbox, test_risk, test_xray_edges, test_xray_service_mutation) | 150 of 298 | 108 / 42 | **72.0%** | 150 / 0 | **100%** | none |

The 3 timeouts, all in `engines_service.py`, are real hangs: two make `_new_id`'s loop never end, and one makes the loop that picks a free connection id never end.

Existing test files the first sets left out were tried on the survivors first:
- `engines_service.py`: the first set already held every test file that calls it.
- `shortcut_service.py`: test_fail_closed, test_shortcut_request_envs, test_shortcut_request_retries, test_shortcut_watch, test_typed_errors. They killed 15, mostly in shortcut requests: a conflict that is raised again, a waiting request found by its id and kind, the requester's name, and what to do next when the shortcut was already there.
- `xray_service.py`: test_audit, test_estate_service_edges, test_optimize_outbox, test_risk, test_xray_edges. They killed 2: what sharing with the central lakehouse returns when the findings were queued and when it failed.

The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **engines_service.py**:
  - A connection in Settings keeping its sign-in method and CLI profile, falling back to the database for Snowflake, and showing as "profile · label".
  - A connection key whose connection part has a colon naming that whole part.
  - A remote engine asking for a confirm unless the profile's policy allows that very engine.
  - Prepare saying the session closes when moving from one local engine to another, and that nothing changes for the same engine or the kernel.
  - The risk check's wording for a stopped warehouse and one with auto-stop off, and for 1 and 6 cells that may change data.
  - A Databricks sync pair that shares only its local or only its remote folder with another being kept, and pair ids of 10 hex characters.
  - A chosen engine whose start was forgotten reading as ready, and a running cell's time to a tenth of a second.
  - Sessions and cells running on daemon threads, so the app can quit.
  - → `test_engines_service_mutation.py`.
- **shortcut_service.py**:
  - The history windows asked of the platform: 30 days and 2,000 changes for the history, 7 days and ten years for the center, ten years for undo.
  - Saving rules returning them, and a repoint across environments returning its warning.
  - A row's first-seen time and its target's environment, and the environments read from the platform's config.
  - The week's count of changes, and environment groups leaving out a layer that is in only one environment.
  - Export making the Downloads folders it needs.
  - Finding a moved target: never listing lakehouses without a name, picking the candidate with more tables, a 300 answer counting as not there, and asking only the first five.
  - Repointing many reporting each warning and problem once.
  - The readers check saying "Matches" when both sides have the same readers.
  - Undo refusing a repoint that recorded no before or after, and an add being undone by its name.
  - A conflicting shortcut being kept on a 400 answer, its message, a shortcut made by hand after the request counting as found, and a twin copy's conflict message.
  - → `test_shortcut_service_mutation.py`.
- **xray_service.py**:
  - A table named with a leading digit getting a letter first, and a table with no id column keyed by its first column.
  - An adopted entity loading new rows by its timestamp column, with "me" as owner when none is known.
  - A dismissed finding's id of up to 1,024 characters and its reason cut at 500.
  - A scan starting unpaused, and pause with no argument pausing it.
  - A scan adding the last 30 days' cost findings, and a second scan writing over the first.
  - Sharing with a central lakehouse that has no name saying "the central lakehouse".
  - Annotating reading the runs log fresh by default, 500 runs.
  - A lakehouse counted as managed or adopted only for its own workspace and tables.
  - A fix that ended in the same second the scan began still counting as fixed.
  - Optimize naming the table with its schema.
  - Vacuum and Optimize assessed under their own label and workload id, without a fresh capacity read.
  - A risk check that can't run being unknown and asking for nothing.
  - A vacuum sized from a recursive listing, and 0 bytes when nothing is listed.
  - Adopt keeping the picked keys, saying which schema and table it came from, and taking the owner from the sign-in.
  - A young staging lakehouse saying how long ago it was written or made, in days.
  - A refresh less than an hour after the last write not showing a staging lakehouse unused.
  - Remove logging the workspace's name and keeping removals from the last 90 days.
  - → `test_xray_service_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `shortcut_service.py` | 98 | `cached is not None and cached is not self._sc_recorded` → `or` | Equivalent: when `all_shortcuts` returns a finished read it always leaves the cache set, so the first test never decides the answer. |
| `shortcut_service.py` | 233 | `zip(shortcuts, hits, strict=True)` → `strict=False` | Equivalent: `hits` is built from `shortcuts` one for one, so the two always have the same length. |
### Real bugs found

- [#2640](https://github.com/kratosdatagod/fabric-desktop/issues/2640): approving a shortcut request counts an "added" change from the same second, just before the request, as the request's own. The request's time drops microseconds and a change's time keeps them, so comparing them as text puts the earlier change after the request.

### Not in this batch

- `engines_service.py`, `shortcut_service.py` and `xray_service.py` past the 150 sampled mutants.
- `meta/store`, `meta/project`, `mailbox`, `cloud_service` and `git/repo`, which batch 14 took.

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

# Mutation testing, batch 17

Three modules no open pull request is changing:
- Fabric user data functions: the function's definition and the parts it is uploaded as (`central/udf.py`).
- Running a notebook in Fabric: the shim a notebook runs with, and the check that blocks or flags a cell before it runs, with its SQL, magics, `%run` order and Preview-only Delta calls (`fabric_run.py`).
- The agent: schedules and their time zone, job outcomes, retries and alerts, the pipeline mailbox, the waiting-request alerts, the shortcut check and the threads it starts (`agent.py`).

`central/checks_service.py` was planned but left out, because it holds the governance promote gate. `agent.py` took its place. `cli.py` was not used because an open pull request changes it.

The run was at `main` 577b43e64 (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 15 (sample cap 150, seed 7), on DuckDB 1.5.6. It ran beside batch 16, which took other modules.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. These tests were left out because they read files outside `sidecar/`:
- `agent.py`: `test_agent_runbook.py` (it reads `docs/`), `test_agent_queue_bound.py::test_a_waiting_request_is_cancelled_by_its_cancel_file_and_is_never_queued`, and seven tests in `test_mailbox.py` that use the example notebook under `examples/` (`test_a_pipeline_request_runs_and_reports_back`, `test_the_same_request_runs_once_however_often_it_is_sent_or_read`, `test_a_cancel_file_stops_a_request_before_it_runs`, `test_a_cancel_that_arrives_between_polls_stops_the_worker_before_it_starts`, `test_cancelling_a_running_request_lets_the_run_finish`, `test_the_notebook_gives_up_cancels_and_fails_after_its_timeout`, `test_the_notebook_names_its_pipeline_run_when_given_one`).
- `fabric_run.py`: none.
- `central/udf.py`: none.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `central/udf.py` | test_udf_parity, test_platform_retries, test_platform_setup_steps, test_platform_refusals, test_requests_core_four (+ test_platform, test_best_practice_checks, test_product_groups, test_trust_badges, test_shared_keys, test_governance, test_central_udf_mutation) | 41 of 41 | 29 / 12 | **70.7%** | 38 / 3 | **92.7%** | 3 equivalent |
| `fabric_run.py` | test_fabric_run_edges, test_compute_check_ast, test_notebook_comments, test_spark_map, test_notebookutils_helpers, test_schedule_config, test_spark_csv_options, test_spark_json_parquet_options, test_conversions_everywhere, test_spark_udf, test_notebook_magics, test_dbutils, test_cloud, test_validate (+ test_spark_refusals, test_spark_merge, test_risk_service_edges, test_integrations_services_edges, test_prove, test_prove_spark, test_fabric_run_mutation) | 150 of 764 | 118 / 32 | **78.7%** | 147 / 3 | **98.0%** | 3 equivalent |
| `agent.py` | test_agent, test_agent_clock_and_folder, test_agent_clock_jumps, test_agent_config_edges, test_agent_crash_report, test_agent_edges, test_agent_fleet, test_agent_queue_bound, test_agent_recovery, test_agent_silent_failures, test_agent_status, test_agent_takeover (+ test_cli_local_commands, test_incident_failures, test_incidents, test_jobqueue_concurrency, test_logs_json, test_mailbox, test_mailbox_edges, test_notify_delivery, test_schedule_clock_changes, test_schedule_edges, test_schedule_edits, test_shared_schedules, test_shortcut_watch, test_agent_mutation) | 150 of 325 | 81 / 69 | **54.0%** | 148 / 2 | **98.7%** | 2 equivalent |

The 3 timeouts, all in `agent.py`, are real hangs: the mutants keep a loop from ever ending.

Existing test files the first sets left out were tried on the survivors first:
- `central/udf.py`: test_platform, test_best_practice_checks, test_product_groups, test_trust_badges, test_shared_keys, test_governance. They killed 8.
- `fabric_run.py`: test_spark_refusals, test_spark_merge, test_risk_service_edges, test_integrations_services_edges, test_prove, test_prove_spark. They killed 16.
- `agent.py`: the files in brackets above, other than test_agent_mutation. They killed 28.

The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **central/udf.py**:
  - The parts a definition is uploaded as: each file once, in order, as inline base64 that decodes back to it, and no parts for no files.
  - The real definition decoding back to itself, with its SQL database as the one connected data source.
  - → `test_central_udf_mutation.py`.
- **fabric_run.py**:
  - The shim naming the workspace, lakehouse and name, and whether schemas are on.
  - A cell the check can't read still being blocked for a literal in-place MERGE, a DROP TABLE, or importing the warehouse helper.
  - A change in a comment after a blank line not counting.
  - A syntax error naming its line and code.
  - A `%sql DROP TABLE` line magic saying where it runs.
  - SQL built from many parts at run time being unknown.
  - With Preview off, a kept DeltaTable's optimize being blocked, while a plain optimize or reading a DeltaTable is fine.
  - The `%run` order naming the first blocking notebook, and `%%scala` not blocking.
  - → `test_fabric_run_mutation.py`.
- **agent.py**:
  - The longest wait for a cron, weekly and daily schedule.
  - The earlier of two times an hour that happens twice when clocks go back.
  - The agent's clock, the status listing 20 jobs, and the shortcut-check and heartbeat threads being daemons.
  - Pruning the queue, home folder and audit trail logging only when something was removed.
  - Which alert addresses can be sent to, each bad one warned about once.
  - Incident routing reading webhooks from the environment and warning once for one that isn't set.
  - The schedule's kept wall time, and a time zone change of exactly a minute.
  - Reconciling schedules: updating a waiting first run, and logging only the kinds of change it made.
  - A deferred schedule saying so only once it is queued, and its backfill being passed on.
  - Only a pipeline job being checked for a take-over, and the mailbox being told a pipeline job's end state or that it was cancelled.
  - The summary's counts, audit times rounded to a tenth of a second, and a failed job's audit time.
  - A retryable error retrying without an alert while tries are left, alerting on the last try, and busy tables retrying without one.
  - A cascading job keeping its tables, and the running job's claim kept through a step.
  - Waiting-request alerts writing no outbox when nothing ever waited, and sending when one is there from before.
  - The shortcut check skipping an environment with no workspace.
  - → `test_agent_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `central/udf.py` | 1018 | `indent=2` → `3` in the definition's JSON | Equivalent: the JSON reads back the same, and nothing compares the text. |
| `central/udf.py` | 1019 | `indent=2` → `3` in the definition's JSON | Equivalent: as above. |
| `central/udf.py` | 925 | `"-" * 60` → `61` in a separator comment | Equivalent: the line is a comment in the generated code. |
| `fabric_run.py` | 334 | `source.rfind("\n", 0, offset)` → `rfind("\n", 1, offset)` in `_in_comment` | Equivalent: it differs only on line 2 of a cell that starts with a blank line, where the column is one too large, and a `#` before the change is still before it. The test for a commented-out change after a leading blank line runs this case. |
| `fabric_run.py` | 677 | `"schema" in names[1:]` → `names[0:]` | Equivalent: `names[0]` is a read call, never `schema`. |
| `fabric_run.py` | 659 | `links[1:]` → `links[0:]` when finding the format | Equivalent: `links[0]` is the read method, never `format`. |
| `agent.py` | 529 | `_enqueue_due` returning `None` for `False` | Equivalent: its one caller only tests it with `not`. |
| `agent.py` | 486 | `due is not None and not s.shared` → `or` | Equivalent: a shared schedule keeps no wall time, so the read gives `None`, and with no due time the wall is never used. |

### Real bugs found

None.

### Not in this batch

- `fabric_run.py` and `agent.py` past the 150 sampled mutants.
- `central/checks_service.py`, left out as above.
- `benchmark`, `prove_service`, `cloud_service` and `git/repo`, which batch 16 took.

# Mutation testing, batch 18

Three modules that no open pull request is changing:
- Databricks SQL's own built-in functions on this PC, which are written as DuckDB SQL or refused by name (`databricks_builtins_sql.py`).
- The Snowflake function checks: each case's reference link, how `run` answers, and the table in the docs (`snowflake_sql_parity.py`).
- The new notebook wizard: its choices, its checks on each answer, and the cells each template writes (`wizard.py`).

`estate_service.py` was planned but left out, because it connects the tests that gate a promote. `integrations/capabilities.py` was left for later.

The run was at `main` afd8d51ba (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 17 (sample cap 150, seed 7), on DuckDB 1.5.6. The branch was then moved onto `main` dbc70faff after batch 17 merged, and its tests were run again there.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. These tests were left out because they read files outside `sidecar/`:
- `databricks_builtins_sql.py`: `test_databricks_coverage.py::test_what_the_docs_say_runs_still_runs`, `::test_the_docs_page_is_up_to_date` and `::test_the_docs_link_the_list`, `test_databricks_sql_parity.py::test_the_docs_carry_the_table`, and, in the wider set below, `test_parity.py::test_docs_page_matches_the_code`, its two `test_a_sql_rows_local_claim_follows_its_function_list` cases and `test_snowflake_sql.py::test_the_docs_table_is_the_codes`.
- `snowflake_sql_parity.py`: `test_snowflake_sql_parity.py::test_the_docs_carry_the_table_and_say_no_account_was_used`, and `test_snowflake_coverage.py::test_what_the_docs_say_runs_still_runs`, `::test_the_docs_page_is_up_to_date` and `::test_the_docs_link_the_list`.
- `wizard.py`: none.

The test files for `databricks_builtins_sql.py` were found by making `rewrite` raise and seeing which files failed. That wider set takes about 80 seconds per mutant, so the sampled run used the two files that test the module most directly (about 28 seconds). The wider set was then run on the survivors.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_builtins_sql.py` | test_databricks_coverage, test_databricks_sql_parity (+ test_academy_dbsql_lab, test_engines, test_local_sql_errors, test_local_sql_mutation, test_parity, test_snowflake_sql, test_spark_builtins_sql, test_spark_hash_refused, test_spark_higher_order, test_spark_integer_casts, test_spark_json, test_spark_lambdas, test_spark_schema_of_json, test_spark_session_functions, test_spark_text_casts, test_untranslatable_sql, test_databricks_builtins_sql_mutation) | 150 of 431 | 130 / 20 | **86.7%** | 146 / 4 | **97.3%** | 4 equivalent |
| `snowflake_sql_parity.py` | test_snowflake_sql_parity, test_snowflake_coverage_edges, test_snowflake_coverage (+ test_snowflake_sql_parity_mutation) | 150 of 243 | 127 / 23 | **84.7%** | 141 / 9 | **94.0%** | 8 equivalent, 1 that needs #2646 |
| `wizard.py` | test_wizard, test_append_callers, test_prove_service_edges (+ test_wizard_mutation) | 150 of 206 | 135 / 15 | **90.0%** | 150 / 0 | **100%** | none |

There were no timeouts.

The wider Databricks set (the files in brackets, other than test_databricks_builtins_sql_mutation) was tried on all 20 survivors first. It killed none. The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **databricks_builtins_sql.py**:
  - Each argument count and its message: `to_time`, `unix_nanos`, `bitmap_and_agg`, `ip_cidr_contains`, `hmac`, `vector_norm`, `time_to_seconds` and `is_valid_utf8` with too few and too many arguments, and "argument" against "arguments".
  - `time_diff` in microseconds counting each microsecond.
  - `to_time` reading a column with a constant format, and refusing a format from a column.
  - `try_make_timestamp_ltz` from a date and a time, read in the zone given or in the session's zone.
  - `bit_reverse` of a literal past INT staying BIGINT.
  - `variant_get` refusing a path that is a number, not a string.
  - A function of a person's own over a window staying as written.
  - A call inside another call being written too, and a statement that is itself the call.
  - → `test_databricks_builtins_sql_mutation.py`.
- **snowflake_sql_parity.py**:
  - A case linking the reference page named by its first word unless it gives one.
  - Each way `run` answers: a statement that runs only in Snowflake with its reason, the documented error counted as the same, another error by its type and first line, no rows as `None`, and only the first column compared.
  - The docs table: its heading sentence, each case that differs, the words for each outcome, and the closing list of the rest.
  - The estimates that differ giving the value their notes name (DATASKETCHES_HLL and APPROX_PERCENTILE).
  - → `test_snowflake_sql_parity_mutation.py`.
- **wizard.py**:
  - The fiscal month choices numbered from 1 with their names.
  - A malformed allowed-values or range line showing its own example.
  - Clean with no columns keeping every column, and with columns selecting them.
  - Load file needing key columns, and load folder refusing another file type.
  - SCD type 2 naming the tracked columns, or any other column when none are tracked.
  - A date dimension covering at most 200 years of 366 days.
  - A fiscal year from January being the calendar year, and one from July named by the year it ends in.
  - → `test_wizard_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `databricks_builtins_sql.py` | 69 | `strict=True` → `False` in `_whole`'s `zip` | Equivalent: `names` is cut to the number of vectors, so both sides are always the same length. |
| `databricks_builtins_sql.py` | 277 | `not isinstance(window, exp.Window) or window.this is not node` → `and` | Equivalent: `counter_diff` in another function's window is still refused with the same message, by the later pass that writes each call. |
| `databricks_builtins_sql.py` | 538 | `isinstance(node.parent, exp.Table)` negated for `variant_explode` in FROM | Equivalent: replacing the call inside its table writes the same SQL, alias included, as replacing the table. |
| `databricks_builtins_sql.py` | 547 | `bfs=False` → `True` when finding the calls to write | Equivalent: in reversed breadth-first order, an inner call still comes before the call around it. |
| `snowflake_sql_parity.py` | 162 | `MD5_NUMBER_UPPER64`'s expected value + 1 | Can't be killed until #2646 is fixed: `same()` compares numbers as floats, so two integers past 2^53 that differ by 1 compare equal. |
| `snowflake_sql_parity.py` | 186 | `_values(1, 0)` → `_values(0, 0)` for `1 / x` | Equivalent: a zero divisor is still there, so the documented error is the same. |
| `snowflake_sql_parity.py` | 391 | COUNT_IF's sample `2` → `3`, and `3` → `4` | Equivalent: two values are still above 1. |
| `snowflake_sql_parity.py` | 392 | MEDIAN's sample `4` → `5` | Equivalent: the median of 1, 2, 3 and 5 is still 2.5. |
| `snowflake_sql_parity.py` | 404 | APPROX_COUNT_DISTINCT's sample `2` → `3` | Equivalent: there are still two distinct values. |
| `snowflake_sql_parity.py` | 405 | HLL's sample `2` → `3` | Equivalent: as above. |
| `snowflake_sql_parity.py` | 411 | APPROX_PERCENTILE's `range(11)` → `range(12)` | Equivalent: DuckDB's estimate at 0.1 is still 1.0, so the case still differs as its note says. |
| `snowflake_sql_parity.py` | 437 | RATIO_TO_REPORT's sample `300` → `301` | Equivalent: the case checks the NULL row, which is still NULL. |

### Real bug found

- #2646: `same()` in `engines/databricks_sql_parity.py`, which the Snowflake and Databricks checks share, compares two whole numbers as floats. Two integers past 2^53 that differ can compare equal, so a wrong 64-bit answer such as `MD5_NUMBER_UPPER64`'s would still be recorded as "same".

### Not in this batch

`estate_service.py`, which connects the tests that gate a promote, and `integrations/capabilities.py`.
