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

Each mutant's tests run with no bytecode cache (`PYTHONDONTWRITEBYTECODE=1`), since #2836. Before
that, Python could reuse the cached code of the previous mutant when the two were the same size and
written in the same second, so a mutant could be scored as the one before it, in either direction.
Scores from batches 1 to 77 were taken without the fix and may be off by such a mutant; batch 78 and
later were taken with it. Two runs with the fix give the same result for every mutant.

Before any mutant, the tests run once on the unmutated copy, and the run stops with a message if they
fail or time out there. Only `sidecar/` is copied, so a test that reads `docs/` or `app/` fails in the
copy whatever the mutant, and without this check every mutant would count as killed. Earlier batches
left such tests out by hand; leave them out (or `--deselect` them) in the same way.

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

# Mutation testing, batch 19

Three modules that no open pull request is changing:
- The metadata project templates: a new project's defaults, who owns the sample key tables and source systems, and the TOML each entity template writes (`meta/templates.py`).
- Source control's service: the defaults each call passes on, the list of repositories this PC opened, the platform repository's reads and deploy refusals, and the note a deploy leaves (`git_service.py`).
- Optimize's app calls: the outbox kept open, which failures are tried again, the overhead a sync records, the central lakehouse's role changes, and the asks before sharing runs or an analysis (`optimize_service.py`).

`integrations/capabilities.py` was started and dropped. Its tests take about 40 seconds per mutant, so after 35 minutes only 41 of 150 mutants had run, and `optimize_service.py` took its place.

The run was at `main` dbc70faff (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 18 (sample cap 150, seed 7), on DuckDB 1.5.6. The branch was then brought up to `main` 352188c0d after batch 18 merged, and its tests were run again there.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. One test was left out because it reads files outside `sidecar/`: `test_vscode_setup.py::test_copilot_instructions_match_the_docs`, in the wider `git_service.py` set.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/templates.py` | test_meta_templates_edges, test_meta, test_meta_project_edges, test_meta_project_mutation, test_landing_zones, test_model_date_table (+ test_meta_templates_mutation) | 84 of 84 | 67 / 17 | **79.8%** | 84 / 0 | **100%** | none |
| `git_service.py` | test_git_service_edges, test_git, test_fabric_git, test_corrupt_state_reads (+ test_capacity_repo, test_git_scopes, test_git_service_more_edges, test_platform_repo, test_vscode_setup, test_notebook_places, test_notebook_folders, test_git_service_mutation, test_git_service_platform_mutation) | 150 of 211 | 76 / 74 | **50.7%** | 149 / 1 | **99.3%** | 1 equivalent |
| `optimize_service.py` | test_opportunities_share, test_optimize_local_runs, test_optimize_outbox, test_optimize_service, test_optimize_service_edges, test_optimize_share_analysis (+ test_access_cache, test_tenant_history, test_opportunities, test_optimize_service_mutation) | 150 of 239 | 126 / 24 | **84.0%** | 150 / 0 | **100%** | none |

There were no timeouts.

The existing tests in brackets were tried on the survivors first. For `git_service.py` they killed 51 of the 74: the sampled set had missed the platform repository, notebook places and capacity repository tests. For `optimize_service.py` they killed none. The new tests killed the rest. The four new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **meta/templates.py**:
  - A new project having the sample by default, and going into a folder that already exists without touching what is there.
  - Only the three-layer preset with the sample getting it: no entities, landing files, key tables or source systems otherwise.
  - The sample key tables, source systems and entities belonging to the owner, or to "me" when there is none.
  - Without keys, the first column being the key.
  - A landing entity reading its source, or a placeholder named after the entity.
  - Clean and fact loading incrementally on the upstream table's watermark, and in full when there is none.
  - A type 2 dimension tracking the first three columns that aren't keys.
  - A custom step owned by its owner, or by "me".
  - → `test_meta_templates_mutation.py`.
- **git_service.py**:
  - The list of repositories keeping the newest 50, and a notebooks place named by its saved name or else its folder.
  - Open, init and create handing back the repository's status.
  - The operations list for one repository or all of them.
  - Each default a call passes on: changing a repository's kind, discarding a block and deleting a branch needing confirm; a notebook diff comparing code only; the log showing 50 commits; a new branch being switched to; a branch with commits no other holds kept without force.
  - A platform read that fails about another operation, or for another reason, not being taken for an older platform.
  - A diff against an environment that isn't one, export keeping prod's key table when dev and prod disagree, only a platform admin deploying, ignored platform files named five at a time, and a deploy noting its commit.
  - → `test_git_service_mutation.py`, `test_git_service_platform_mutation.py`.
- **optimize_service.py**:
  - The outbox opened once, and discover counting the tables that aren't any family's.
  - Connecting saying it created nothing.
  - A refused write, including a network error that says 403, not being tried again, and the answer being `False`, not just falsy.
  - A sync recording the rows and commits it added.
  - A connection saved without the schemas flag being read as having schemas, both for a family's tables and for keeping them from DefaultReader.
  - The reason a role read failed being cut to 200 characters.
  - A role write refused with either `PreconditionFailed` or status 412 saying the roles changed, and any other failure raised as it is.
  - Sharing runs reading the whole history, and sharing runs or an analysis needing approve as well as the confirm.
  - Each confirm being 32 hex characters, the ask naming the lakehouse, and an analysis's confirm not depending on key order.
  - Metadata runs coming only from environments kept on this PC (Dev by default), and a run log row without its environment getting the one it was read from.
  - → `test_optimize_service_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `git_service.py` | 84 | `json.dumps(..., indent=2)` → `indent=3` for the list of repositories | Equivalent: the file is only ever read back as JSON. |

### Real bugs found

None.

### Not in this batch

`integrations/capabilities.py`, for the time its tests take per mutant.

# Mutation testing, batch 20

Three modules that no open pull request is changing:
- The proof's Spark side: the names and paths it accepts, the versions it reads, the SQL it rewrites, and the readers it patches so that a proof only changes its own folder (`proof_spark.py`).
- Structured streaming on this PC: what each sink reports as written, the query's progress and status, and the files a folder stream reads (`spark_stream.py`).
- Surrogate key tables: how many times a key commit is retried and how long it waits, the check on the keys a batch just added, the key service's answers, key counts, and source systems (`meta/keys.py`).

`engines/fabric_spark.py` was planned but left out, because #2417 is changing it. `spark_stream.py` took its place. Batch 19's modules were left to batch 19.

The run was at `main` 352188c0d (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 19 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. No test that reads `docs/`, `examples/`, `app/` or the module's own source was in these sets, so none was left out.

`test_prove.py` also uses `proof_spark.py`, but it is slow, so the sampled run left it out. It was run on the 10 survivors and killed none. In the same way, the other test files that use streams were run on the 22 `spark_stream.py` survivors and killed none, and `test_estate`, `test_meta_service_edges`, `test_platform_service_paths` and `test_platform` were started on the 34 `meta/keys.py` survivors and had killed none of the first two when they were stopped, so the new tests were written for all 34.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `proof_spark.py` | test_prove_spark, test_prove_spark_install (+ test_proof_spark_mutation) | 150 of 397 | 140 / 10 | **93.3%** | 149 / 1 | **99.3%** | 1 equivalent |
| `spark_stream.py` | test_spark_stream, test_spark_stream_edges (+ test_spark_stream_mutation) | 150 of 259 | 128 / 22 | **85.3%** | 147 / 3 | **98.0%** | 3 equivalent |
| `meta/keys.py` | test_keys, test_meta_keys_edges, test_shared_keys, test_meta_service_keys_edges, test_meta_atomic_save, test_meta_source_systems_edges (+ test_meta_keys_mutation) | 150 of 241 | 116 / 34 | **77.3%** | 149 / 1 | **99.3%** | 1 equivalent |

There were no timeouts. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **proof_spark.py**:
  - `dbo` naming a table on a lakehouse without schemas, in any case, and another schema being refused.
  - A `file:` path without a slash after the colon being the lakehouse mount too.
  - A `Tables/` folder being one name without schemas and two with them, and a deeper folder being refused.
  - A table pinned without a version still existing.
  - A frame reading the pinned version, and a table the proof made reading its latest.
  - Dropping a view the notebook didn't make being refused, alone or listed with one it did make.
  - A table name with no place in the SQL being refused rather than left as it was.
  - A load outside the lakehouse handing back Spark's own frame.
  - Every path given to `parquet` being checked, but only the first argument of the other readers, whose second argument is not a path.
  - → `test_proof_spark_mutation.py`.
- **spark_stream.py**:
  - A table sink reporting its rows, its query name (or none) and a trigger time in milliseconds.
  - A console sink reporting its rows, and `foreachBatch` reporting no row count.
  - A folder sink making missing folders, writing again, and naming its files `part-NNNNN-xxxxxxxx.parquet`.
  - A Delta sink at a `Tables/` path writing that table.
  - The batch message naming the versions read.
  - A folder stream reading only files of its format, not other files or a folder named like one.
  - Two streams joined being refused as something that runs in Fabric.
  - The query's last progress, its error and its stopped status; `queryName` and `options` returning the writer; `False` not being a set flag; and a file without partitions matching every row.
  - → `test_spark_stream_mutation.py`.
- **meta/keys.py**:
  - Making a key table and adding keys each trying 10 times, waiting longer each time, before giving up.
  - The unknown member being dated 1900-01-01, and a key table that has it not being written again.
  - A batch of keys already there adding none, and reporting so in full.
  - Keys following a 0 key, and reading a table that isn't there finding none.
  - A key that another run gave the same business key meanwhile stopping the batch.
  - Key counts for a missing table, a table with only its unknown member, and a table without it.
  - Only a store with both `issue` and a client being the platform's key service, and that service's lookups, counts and new keys, with unknown members and empty values left out.
  - A one-column business key in Spark SQL being just trimmed.
  - Source systems being active and id 0 unless they say otherwise, from a dict and from the file.
  - Files being written into a project that has no keys folder yet, and the path coming back.
  - → `test_meta_keys_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `proof_spark.py` | 196 | `tree.this is not None` → `is None` in the DROP VIEW fallback | Equivalent: sqlglot always sets `tables` on a DROP, so the fallback after `or` is never read. |
| `spark_stream.py` | 101 | `history(now - last)` → `history(now + last)` | Equivalent: reading more history only adds older versions to a dict looked up by the versions after `last`. |
| `spark_stream.py` | 104 | `range(last + 1, …)` → `range(last + 0, …)` | Equivalent: version `last` is the one already loaded, so it has no removed and no new files. |
| `spark_stream.py` | 332 | `explain(extended=False)` → `True` | Equivalent: `explain` prints the same sentence whatever `extended` is. |
| `meta/keys.py` | 432 | `else 0` → `else 1` when no unknown member row comes back | Equivalent: `read_where` gives `None` only for a table that isn't there, and `key_stats` has already answered for a missing table, so only a table deleted between the two reads could reach it. |

### Real bugs found

None.

### Not in this batch

`engines/fabric_spark.py`, which #2417 is changing, and `integrations/services.py`.

# Mutation testing, batch 21

Three modules that no open pull request is changing:
- Warehouse projects in source control: the size and count limits and the messages that name them, how deep projects and files may sit, which folders are searched, names read from the text, and which environment a deploy counts as (`warehouse_project_service.py`).
- What an outside tool is given: page limits, the thin adapters to the engine, executions, what notebook runs and proofs start with, and which fields reach the tool (`integrations/services.py`).
- The notebook's own Python process: requests and their answers, the snapshot a starting cell shows, polls, stop, restart and close, the result viewer's requests, and how exit codes and limits are written (`kernel_proc.py`).

`integrations/services.py`, left out of batch 20, is in this one.

The run was at `main` adb130d08 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 20 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Four tests in `test_integrations_prompts.py` read pages in `docs/` and failed there, so they were deselected from the `integrations/services.py` run. Every other test that was kept passed both ways, so no run below can score a false 100%.

`test_integrations_executions.py` and `test_integrations_conformance.py` also use `integrations/services.py`, but each takes about 40 seconds, so the sampled run left them out. `test_integrations_executions.py` was run on the 11 survivors in the notebook run and proof start checks and killed 9; the other two only drop `retryable=True`. For `kernel_proc.py`, the existing tests of the outside tool's environment (`test_integrations_executions.py`) and of exit codes (`test_notebook_out_of_room.py`) were missed by the first run; they were run on its 49 survivors and killed 12. The new tests were written for the rest.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse_project_service.py` | test_warehouse_project, test_warehouse_project_edges, test_warehouse_project_more_edges (+ test_warehouse_project_mutation) | 150 of 340 | 129 / 21 | **86.0%** | 149 / 1 | **99.3%** | 1 equivalent |
| `integrations/services.py` | test_integrations_services_edges, test_integrations_core, test_integrations_workspace_scope, test_integrations_registry, test_integrations_prompts, test_integrations_mcp (+ test_integrations_services_mutation) | 150 of 203 | 59 / 91 | **39.3%** | 150 / 0 | **100%** | none |
| `kernel_proc.py` | test_kernel_process, test_kernel_process_edges (+ the four existing tests above, test_kernel_proc_mutation) | 150 of 505 | 101 / 49 | **67.3%** | 143 / 7 | **95.3%** | 2 Windows only, 5 equivalent or timing |

The one timeout was in `kernel_proc.py`: stop's wait for the reader, negated, waits its full 5 seconds. The three new test files pass on DuckDB 1.5.6 and 2.0.0. The `kernel_proc.py` tests that start a real process end it in a `finally` block.

### What the survivors were, and the tests that now kill them

- **warehouse_project_service.py**:
  - 5000 objects being taken and 5001 refused, for writing and for comparing.
  - A file of exactly 1 MB being read and one byte more refused, and the read, save and diff messages naming 1024 KB.
  - A diff taking 2 MB in all.
  - A small project having no note about being cut.
  - Projects being found four folders down and no deeper, and a folder deeper than that being refused.
  - Hidden folders, build folders (in any case) and files named like projects holding no projects.
  - At most 50 projects being listed from one repository.
  - A project being named from its `.platform` file, and the repository by its name or else its folder.
  - A file seven folders deep being allowed and eight refused, and a link to nowhere being refused.
  - Quotes inside a bracketed name being part of the name.
  - A table without a schema being skipped for that reason, when writing and when comparing.
  - A project made twice keeping its first `.platform` file.
  - `prod` sent without a workspace counting as no environment, so the deploy is gated.
  - → `test_warehouse_project_mutation.py`.
- **integrations/services.py**:
  - A page being at most 500 and at least 1, and the default page sizes of each listing, from the first row.
  - Listings asking the engine for everything before paging.
  - About 20 adapters handing back what the engine gave and passing what the caller sent.
  - Pipeline run settings, and a zero timeout meaning none.
  - A step an outside tool can only mock saying so.
  - Notebook dependencies and environment coming from the saved notebook.
  - Proofs, side-by-side runs and receipts coming back without data, and metadata and notebook fields that reach a tool, with the metadata project named or "Metadata project".
  - Cell line counts, and source hidden when asked.
  - The execution manager being made once, SQL and Python run titles, targets, waits, timeouts and row limits, and a Python run taking a whole lakehouse or none.
  - Execution status waiting only when asked and leaving out the logs, and logs, lists and cancels going to the manager.
  - What a notebook run and a proof are started with, and the message for a notebook with no code.
  - A receipt export hiding what the caller asked and what policy requires.
  - → `test_integrations_services_mutation.py`.
- **kernel_proc.py**:
  - A request without a process being refused and starting none, and a request returning its result or raising the error it is answered with, by message or else by type.
  - The variable libraries call answering nothing when the engine gives nothing.
  - A starting cell showing running, with the next count and no outputs, and a cell that ends its process before starting showing those outputs.
  - A cell not starting while another notebook's cell runs in a shared session, naming that notebook or "Another notebook".
  - Another notebook's poll asking the running process without starting one, with its timeout.
  - A poll without outputs keeping the outputs already shown, and a poll that gets no answer showing the cell as it last was.
  - A process that ends only ending a cell that is running, and a restart clearing the count and the cell.
  - The debugger's timeout, and the result viewer's defaults and answers.
  - Memory being read in bytes from the kilobytes Linux reports.
  - The reader thread being a daemon, stop waiting for a slow reader, and close ending a process that runs.
  - A cell inside its time limit not being interrupted.
  - Exit codes shown in hex only past four hex digits, signed or unsigned, and limits written in their largest whole unit, in GB from 1 GB up.
  - → `test_kernel_proc_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `warehouse_project_service.py` | 95 | `and` ↔ `or` in `_unquote` | Equivalent: `_PART` only matches a balanced bracket or quote form or a plain identifier, so both forms give the same answer. |
| `kernel_proc.py` | 837 | `JOB_PIDS = 1024` → `1025` | Windows only: the job object's process list. |
| `kernel_proc.py` | 838 | `PROCESS_QUERY_LIMITED_INFORMATION` `0x1000` → `0x1001` | Windows only: `OpenProcess` access rights. |
| `kernel_proc.py` | 313 | `proc is self._proc and self.pid` → `or` in the memory watch | Equivalent once the process has said its id; before that, under a second, the watch measures nothing instead of the launcher. |
| `kernel_proc.py` | 468 | the 5 second wait after a cell ends its process → 6 | Timing: the reader marks the process gone well inside either. |
| `kernel_proc.py` | 481 | `or` → `and` when another notebook polls | Equivalent: an ended process makes the request fail, and that gives the same idle answer. |
| `kernel_proc.py` | 533 | stop's 5 second wait for the reader → 6 | Timing, as at line 468. |
| `kernel_proc.py` | 630 | `spawn=False` → `True` for `frame_wrangle` | Equivalent: `alive()` is checked just before, so a process is always running. |

### Real bugs found

None.

### Not in this batch

`meta/source_systems.py`, kept as a substitute, wasn't needed.

# Mutation testing, batch 22

Three modules, the first three on this batch's list:
- Schedules on this PC: the runs log's limits, the saved file, how `schtasks` is started, pausing and resuming under the signed-out policy, renaming a notebook, and the next run when the clocks change (`local_schedules.py`).
- Pipeline definitions: the reader's version and limits, the size messages, where a pipeline takes its name from, Git folders, Switch cases, and how each format is exported (`orchestration/definition.py`).
- The agent's job queue: its defaults, what each call answers, the backoff, recovering leases and retries from before the clock went back, why a job can't be requeued, pruning, and a queue file damaged inside (`jobqueue.py`).

`cleanup.py` and `sqlcompat/practices.py` were left for a later batch. Batch 21's modules were left to batch 21.

The run was at `main` e8b14fb72 (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 20 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_activity_registry.py::test_example_report_in_docs_is_current` and `test_mailbox.py` read `examples/` or `docs/`, so they were left out. `test_agent_queue_bound.py::test_a_waiting_request_is_cancelled_by_its_cancel_file_and_is_never_queued` was left out of the `jobqueue.py` run as well.

The sampled runs left out some slower test files that also use these modules, and those files were then run on the survivors:
- For `local_schedules.py`, the other schedule and notebook test files killed 6 of the 23 survivors.
- For `orchestration/definition.py`, the other pipeline test files killed 2 of the 32.
- For `jobqueue.py`, the agent's test files had killed 3 of the first 6 of the 57 survivors when they were stopped, so the new tests were written for all 57.

`jobqueue.py` has 137 places to mutate, fewer than the cap, so every one was run.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `local_schedules.py` | test_local_schedules_edges, test_schedules, test_schedule_config, test_schedule_edges, test_schedule_locks_and_lists, test_schedule_service_mutation, test_signed_out_schedules, test_pipeline_schedules, test_json_files (+ test_schedule_clock_changes, test_shared_schedules, test_schedule_edits, test_notebook_folders, test_notebook_runs, test_local_schedules_mutation) | 150 of 248 | 127 / 23 | **84.7%** | 146 / 4 | **97.3%** | 4 equivalent |
| `orchestration/definition.py` | test_orchestration_definition_edges, test_pipeline_definitions, test_pipeline_fabric_parity, test_pipeline_gotchas, test_deep_nesting, test_pipeline_adapters_edges (+ 13 other pipeline test files, test_orchestration_definition_mutation) | 150 of 253 | 118 / 32 | **78.7%** | 148 / 2 | **98.7%** | 2 equivalent |
| `jobqueue.py` | test_jobqueue_concurrency, test_jobqueue_edges, test_agent_queue_bound, test_sqlite_connections_closed, test_store_upgrades, test_schedule_clock_changes, test_shared_schedules, test_schedule_edits (+ test_jobqueue_mutation) | 137 of 137 | 80 / 57 | **58.4%** | 136 / 1 | **99.3%** | 1 equivalent |

There were no timeouts. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **local_schedules.py**:
  - The runs log keeping 10,000 rows, trimmed at 8 MB down to 4 MB, those limits being handed to the trimmer, and a run being logged into folders that weren't made yet.
  - The schedules file being saved with a one-space indent, and the start time being on the minute.
  - `schtasks` being started as text with its output captured, and with no window flag where Python has none.
  - A signed-out schedule being paused under a policy that forbids signed-out runs, but not resumed.
  - `set_enabled` and `set_signed_out` answering with the schedule.
  - Pausing disallowed schedules doing nothing when the policy allows them.
  - Renaming a notebook moving only that notebook's schedules (not a pipeline's with the same name) and saving them, and leaving the file alone when no schedule runs it.
  - The next run when the clocks go back (the repeated hour, at either of its two moments) and when they go forward, in Europe/London. These tests are skipped where Python can't set the time zone (Windows).
  - → `test_local_schedules_mutation.py`.
- **orchestration/definition.py**:
  - The reader being version 1 and the size limit being 20 MB, with the limit named in megabytes in the messages for text and for a file.
  - 40 containers deep, of ForEach or Switch cases, being read and 41 being too deep.
  - Only the `Inactive` state marking an activity inactive, and a Switch case that isn't an object being skipped.
  - The parts listed in the schema, and a Synapse resource type alone reading as Data Factory.
  - A pipeline taking its name from the wrapper, the `.platform` part, the file, or a Git folder's `.platform` file, in that order.
  - A Git folder skipping folders and files over the limit inside it.
  - Export:
    - A changed definition being written with a two-space indent and its own characters.
    - A pipeline's content never taking parts from a wrapper it didn't come from.
    - An item definition's content part being chosen over a `.platform` part before it.
    - A broken `.platform` part naming its place.
  - → `test_orchestration_definition_mutation.py`.
- **jobqueue.py**:
  - A backoff of a minute, doubling up to half an hour, and three attempts unless told otherwise (at least one).
  - The queue file being made in a folder that doesn't exist yet, and opened again.
  - A job id being the time and 12 hex digits, a delayed job being due later, and a job already queued under the same key coming back marked as such.
  - A claim's lease ending at the time of the claim plus the lease.
  - A retry further off than the longest backoff plus five minutes being due, and one nearer not being due.
  - `fail`, `retry_later`, `cancel`, `requeue` and `set_payload` each answering as documented.
  - Recovering a lease further off than the longest lease plus the margin, and leaving one within it.
  - `requeue` refusing an unknown job, a job that isn't dead or failed, a job from a Fabric pipeline, and a job with a newer run queued, each with its own message, and putting back a dead job with or without a key.
  - Pruning by age (a day being 86,400 seconds), and `dry_run` only listing.
  - `list` and `for_env` defaulting to 50 and 200 jobs.
  - A queue file that opens but fails its check being set aside.
  - → `test_jobqueue_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `local_schedules.py` | 86 | `replace(microsecond=0)` → `microsecond=1` | Equivalent: the start time is written with `%S`, which drops microseconds. |
| `local_schedules.py` | 121 | `nxt + k * step` → `nxt - k * step` | Equivalent: `k` runs from `-span` to `span`, so the same grid times are made in another order, and the earliest one still to come is taken. |
| `local_schedules.py` | 125 | `range(7)` → `range(8)` for a daily schedule | Equivalent: `weekday()` is never 7. |
| `local_schedules.py` | 126 | `not times or not days` → `and` | Equivalent: with no times or no days, the loop after it finds nothing and returns `None` anyway. |
| `orchestration/definition.py` | 174 | `_dump(ensure_ascii=False)` → `True` | Equivalent: `_dump` is only compared with another `_dump`, so both sides change together. |
| `orchestration/definition.py` | 454 | `ensure_ascii=False` → `True` for the parts JSON | Equivalent: the parts are paths and base64 payloads, which are ASCII. |
| `jobqueue.py` | 121 | `sqlite3.connect(timeout=30)` → `31` | Equivalent in any test: it only changes how long a connection waits for another writer's lock. |

### Real bugs found

None.

### Not in this batch

`cleanup.py` and `sqlcompat/practices.py`, and batch 21's modules.

# Mutation testing, batch 24

Three modules, the first three on this batch's list:
- Platform skins: the size of a skin file, how many pages the rail and its labels hold, the limits on descriptions and targets, a label for a command that doesn't exist, which colour pairs a mode must set, the contrast maths, and how a skin is exported (`skins.py`).
- The agent's settings file: the defaults of each section, the lowest value of each number, the shortcut webhook, incident routes, shared schedules, a service principal's secret, and how long names are cut in messages (`agent_config.py`).
- Side by side runs: the saved history, the cores choice, starting a run, polling each cell on this PC, waiting on Fabric, the timings and cost, the upload speed, the tables a notebook writes, and copying a record that is changing (`side_by_side_service.py`).

`appstudio/providers.py` was left for a later batch, because App Studio has open pull requests. Batch 23's modules were left to batch 23.

The run was at `main` 7ce636067 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 22 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Six tests in `test_skins.py` and two in `test_parity.py` read `app/` or `docs/`, and `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` reads `examples/`, so they were left out.

The sampled runs left out some other test files that also use these modules, and those files were then run on the survivors:
- For `agent_config.py`, the incident, shortcut, shared schedule and agent test files killed 28 of the 65 survivors.
- For `side_by_side_service.py`, the other side by side, notebook, OneLake and service test files killed 6 of the 39.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `skins.py` | test_skins, test_parity (+ test_skins_mutation) | 150 of 173 | 136 / 14 | **90.7%** | 149 / 1 | **99.3%** | 1 equivalent |
| `agent_config.py` | test_agent_config_edges, test_schedule_config, test_agent (+ test_incidents, test_shortcut_watch, test_shared_schedules, test_agent_mutation, test_agent_silent_failures, test_schedule_edges, test_agent_status, test_file_text_one_line, test_agent_config_mutation) | 150 of 267 | 85 / 65 | **56.7%** | 148 / 2 | **98.7%** | 2 equivalent |
| `side_by_side_service.py` | test_side_by_side, test_side_by_side_edges (+ test_side_by_side_cores, test_side_by_side_more_edges, test_json_files, test_onelake_writes, test_notebook_cells, 7 service test files, test_side_by_side_service_mutation) | 150 of 220 | 111 / 39 | **74.0%** | 150 / 0 | **100%** | none |

There were no timeouts. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **skins.py**:
  - A file of exactly 64 KB being read and one byte more being refused.
  - The rail and its labels holding 12 pages and not 13, and labels that aren't an object being refused.
  - A description and a target's basis holding 200 characters, and targets holding 4 and not 5, as a list only.
  - A label for an unknown command being one problem, not a crash.
  - A token outside every contrast pair needing no partner in a mode without built-in colours, and setting only the background of a pair naming the missing text token.
  - Short hex colours meaning the same as long ones, and each channel carrying its WCAG weight.
  - An export being two-space JSON in the manifest's own key order.
  - → `test_skins_mutation.py`.
- **agent_config.py**:
  - The pipeline, incidents and shortcut sections being off until written `true`, with their defaults, and three attempts by default.
  - Each of 16 numbers having its default, taking its lowest value, and refusing anything below it as one problem; a number that isn't one being a problem, not a crash.
  - The problem count in words, and a read config that can't be changed.
  - Shortcut breaks going to their own webhook, else the alerts one.
  - Routes being read with their filters, and a route with a URL, without `webhook_env`, with an unknown key, without a name, named `default` or not a table each being one problem named by its place.
  - Escalation naming a route that was read.
  - A schedule without an id named by its place, and long ids, environments, certificate paths and project folders cut to 80 or 200 characters in messages.
  - Shared schedules: reading when the mailbox takes their environment, needing the pipeline on, a backfill needing `allow_backfill`, and an id of at most 40 characters.
  - A service principal reading with a secret variable and needing a certificate or a secret variable, and a project path the system refuses not being a project.
  - → `test_agent_config_mutation.py`.
- **side_by_side_service.py**:
  - Fabric being asked every 5 seconds and given up on after 6 hours (with a stand-in clock).
  - A saved cores choice being kept only when it is one of the choices, a good choice being saved and answered with the new card, a bad one saving nothing, and a PC that can't count its cores taking one.
  - A clean history file being read without being set aside, a torn one staying in place when the repair can't write, the history keeping the newest 200, and the list showing the newest 20.
  - A reason a notebook can't run naming its cell when it has one, a save that doesn't name every cell stopping the start, and a started run having a dated id and running on a background thread.
  - A held run getting a kernel process of its own and closing it, each cell being polled from the start with its last output kept, the error message cut to 1,000 characters, and cells numbered from one.
  - The Fabric run's times rounded to the hundredth, its CU seconds and cost per hour of capacity, and a start-up split never below nothing.
  - Upload time and speed rounded to the hundredth, with a speed for an upload under a second.
  - Writes read from code cells only and named from cell one.
  - A record that keeps changing being tried 20 times and then called busy.
  - → `test_side_by_side_service_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `skins.py` | 176 | `return False` dropped in `_command_id` | Equivalent: the only caller that reads the answer tests it with `not`, and `None` is falsy too. |
| `agent_config.py` | 305 | `or` → `and` inside a shared schedule's `every_minutes` check | Equivalent: the checks before it already refuse an `every_minutes` that isn't a whole number from 1. |
| `agent_config.py` | 305 | `minutes < 1` → `minutes < 0` | Equivalent for the same reason: the earlier check refuses 0. |

### Real bugs found

None.

### Not in this batch

`appstudio/providers.py`, and batch 23's modules.

# Mutation testing, batch 23

Three modules, the first three on this batch's list:
- Clean up: the titles and texts of each tidy-up, which lines of an f-string are left alone, leading blank lines, frozen cells, and an empty cell whose change wasn't picked (`cleanup.py`).
- SQL best practices: TOP n PERCENT before a star, UPDATE ... SET with and without FROM, LIKE inside IIF and inside grouping brackets, NOT IN over an expression, aliases and calls in correlated subqueries, table-valued functions, CTE names, a TRUNCATE then an INSERT without INTO, and statements that end early (`sqlcompat/practices.py`).
- Executions a connected tool starts: the output, row, log and record limits, how the list is read and sorted, how a stop and a cancel wait, a query error that isn't a stop, numbering cells, a failed cell, the time a call waits, and daemon threads (`integrations/executions.py`).

`integrations/registry.py` was left for a later batch. Batch 24's modules (skins, agent configuration, side by side and App Studio providers) were left to batch 24.

The run was at `main` 7ce636067 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 22 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` and `test_practice_docs.py` read `docs/`, so they were left out of the `sqlcompat/practices.py` run.

`cleanup.py` only changes cell text in memory: it writes no file and deletes nothing. Its new tests, like the others, point `LOCALAPPDATA` and `HOME` at the test's own temporary folder.

The `cleanup.py` survivors that were left were also run against `test_service_edges.py` and `test_batches.py`, which call clean up through the service; neither killed any.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cleanup.py` | test_cleanup, test_cleanup_edges (+ test_cleanup_mutation) | 150 of 317 | 133 / 17 | **88.7%** | 145 / 5 | **96.7%** | 5 equivalent |
| `sqlcompat/practices.py` | test_sql_practices, test_learn_fixes, test_learn_guides (+ test_sqlcompat_practices_mutation) | 150 of 547 | 109 / 41 | **72.7%** | 138 / 12 | **92.0%** | 12 equivalent |
| `integrations/executions.py` | test_integrations_executions, test_integrations_executions_edges, test_attach_race (+ test_integrations_executions_mutation) | 150 of 157 | 116 / 34 | **77.3%** | 147 / 3 | **98.0%** | 3 equivalent |

Two `executions.py` mutants timed out, and were counted as killed: one keeps a call waiting after its work has finished, and the other makes a stop wait on a kernel that isn't running. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **cleanup.py**:
  - "1 unused import" and "2 unused imports" in the titles.
  - Three cells listed as "Cells 1, 2 and 3".
  - The number of print lines counted from each cell's code.
  - "Cell 2 has no code." and "Cells 2 and 3 have no code."
  - An empty cell being kept when its change isn't one of those picked.
  - Leading blank lines being removed.
  - A frozen cell getting no spacing change.
  - The lines of an f-string over several lines being left alone up to, but not including, the line that closes it.
  - → `test_cleanup_mutation.py`.
- **sqlcompat/practices.py**:
  - The rules being frozen.
  - A star after `TOP 10` and after `TOP 10 PERCENT` in a view.
  - `UPDATE orders SET ...`, with or without WHERE, naming a table without its schema, and `UPDATE o SET ... FROM dbo.orders AS o` not.
  - `LIKE '%x'` inside brackets in WHERE counting, inside `IIF(...)` not, and in a WHERE clause on its own.
  - `NOT IN (SELECT b + 1 ...)` being flagged, since an expression can't be shown to drop NULLs.
  - A column called `row_number` not being `ROW_NUMBER()`.
  - A star in a subquery not feeding an INSERT, and a JOIN without ON inside brackets.
  - `CROSS JOIN` at the start being flagged once.
  - A table-valued function and a CTE name not being tables without a schema.
  - Subqueries in the SELECT list: aliases with and without AS, `dbo.u` defining `u`, a schema-qualified table, `dbo.fn(...)`, and an outer alias anywhere in the subquery.
  - `TRUNCATE TABLE dbo.t` then `INSERT dbo.t` (without INTO) being a reload.
  - Statements that end early (`TRUNCATE`, `... INSERT`, `... AS`, `... NOT IN (`, `... ORDER`, `WHERE YEAR(d)`, `VARCHAR(MAX`) being read without an error.
  - → `test_sqlcompat_practices_mutation.py`.
- **integrations/executions.py**:
  - A displayed table keeping 100 rows, the cut naming 100,000 characters, 2,000 log lines, and 200 finished records kept.
  - The list holding 50 records by default, reading the newest 200 files, sorting by start time with no start time as 0, and leaving out a running execution's result.
  - A record another save already removed being no error.
  - A view's seconds being counted from the start, to three places.
  - A query timeout being retryable and a cancel not.
  - Cells numbered from 1, the log saying so, and a failed cell not being retryable.
  - An error output keeping its traceback, and an image being named by its type.
  - A stop interrupting the kernel, waiting while it reports running, and only then ending it.
  - A cancel waiting for the work to finish.
  - A query error that isn't a stop reaching the caller, with the query slot given back.
  - A call with a time limit answering a second before it.
  - The execution, its timer and a query's stop running on daemon threads.
  - → `test_integrations_executions_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `cleanup.py` | 256 | blank-line count starts at 1, not 0 | Equivalent: only leading blank lines are affected, and those are removed anyway. |
| `cleanup.py` | 267 | `1 not in keep` → `0 not in keep` | Equivalent: line 1 is only kept when a string starts on it, so it isn't blank. |
| `cleanup.py` | 295 | `lambda i: True` → `False` when nothing is chosen | Equivalent: `plan` only reads the changes found, not the tidied cells. |
| `cleanup.py` | 337 | `ch["id"] == "empty_cells"` → `!=` | Equivalent: with no empty cells, the list of cells to remove is empty either way. |
| `cleanup.py` | 428 | `return False` → `return None` | Equivalent: the result is only used as a condition. |
| `sqlcompat/practices.py` | 248, 253, 333, 369 | `return False` → `return None` | Equivalent: each result is only used as a condition. |
| `sqlcompat/practices.py` | 351 | `return ""` → `return None` | Equivalent: the result is only compared with `"WHERE"`. |
| `sqlcompat/practices.py` | 376 | subquery range starts at the bracket, not after it | Equivalent: the bracket is neither a name nor FROM or JOIN. |
| `sqlcompat/practices.py` | 382 (2) | table-name range starts one or two places earlier | Equivalent: those places are FROM or JOIN and the token before it, which can't start a `name.name` reference. |
| `sqlcompat/practices.py` | 387 | `and` → `or` in the alias check | Equivalent in any T-SQL: the extra "aliases" are words such as WHERE or JOIN, which can't start a reference. |
| `sqlcompat/practices.py` | 391 (2) | `j == paren + 1` → `paren - 1` or `paren` | Equivalent: the token after the bracket is SELECT, and the token before any other is checked anyway. |
| `sqlcompat/practices.py` | 392 | `j + 3 >= close` → `j - 3 >= close` | Equivalent: `j + 3` is at most the closing bracket, which is neither a dot nor an opening bracket. |
| `integrations/executions.py` | 85 | after a cut, the budget left is 1, not 0 | Equivalent: no output is 1 character long, so every later one is cut either way. |
| `integrations/executions.py` | 277 | a cancel waits 3 × the grace period, not 2 | Only changes how long a cancel waits for work that hasn't stopped; a test of it would be slow and timing-dependent. |
| `integrations/executions.py` | 295 | `have_slot = False` → `True` | Equivalent: the first try for the slot sets it before anything can fail. |

### Real bugs found

None.

### Not in this batch

`integrations/registry.py`, and batch 24's modules.

# Mutation testing, batch 25

Three modules, the three on this batch's list:
- The capability registry: the version a capability starts at, when removing one counts as a change, the timeout limit, paging defaults, a preview capability's status, and the message for an unknown resource option (`integrations/registry.py`).
- The optimization analyzers: how cells are normalized, the run history text, which duplicate notebook is kept and which copies count as running, the tables evidence, how copy sources are compared, the wording for each kind of repeated copy, and the SQL scan limit (`opportunities/analyzers.py`).
- The shortcut command center: storage target keys, an unreadable lakehouse, allowed connections, port paths without a schema, cross-environment risk text, accepted risks, needs, counts, the readers gap, CSV cells, and environment twins (`shortcut_center.py`).

`integrations/http_server.py` was left to batch 26, which ran at the same time.

The run was at `main` a09aa1fe0 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 24 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Six tests read `docs/`, so they were left out of the `integrations/registry.py` survivor run: `test_activity_registry.py::test_example_report_in_docs_is_current`, `test_integrations_conformance.py::test_capability_ids_in_docs_are_real`, and four in `test_integrations_prompts.py` (`test_workflows_page_matches_the_prompts`, `test_connect_page_carries_every_setup_from_the_app`, `test_agent_instructions_name_every_prompt_and_only_real_tools`, `test_kratosforge_skill_names_only_real_tools_and_every_prompt_and_matches_its_page`).

The first `integrations/registry.py` run used its four fastest test files. The 45 survivors were then run against every `test_integrations_*.py` file and `test_activity_registry.py`, which killed 35. Before writing new tests for `shortcut_center.py`, its survivors were run against `test_fail_closed.py`, `test_shortcut_service_mutation.py` and `test_shortcut_service_paths.py`. The last two killed 6; `test_fail_closed.py` killed none.

None of these modules writes files. The new tests, like the others, point `LOCALAPPDATA` and `HOME` at the test's own temporary folder, and use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/registry.py` | test_integrations_registry, test_integrations_mcp, test_integrations_permissions, test_integrations_http; survivors also against every other test_integrations_* file and test_activity_registry (+ test_integrations_registry_mutation) | 150 of 235 | 105 / 45 | **70.0%** | 150 / 0 | **100%** | none |
| `opportunities/analyzers.py` | test_opportunities; survivors also against test_opportunities_share and test_optimize_share_analysis (+ test_opportunities_analyzers_mutation) | 150 of 183 | 101 / 49 | **67.3%** | 146 / 4 | **97.3%** | 4 equivalent |
| `shortcut_center.py` | test_shortcut_center, test_shortcut_center_more_edges, test_shortcut_fixes, test_shortcut_request_envs, test_shortcut_request_retries, test_shortcut_service_mutation, test_shortcut_service_paths (+ test_shortcut_center_mutation) | 150 of 287 | 116 / 34 | **77.3%** | 150 / 0 | **100%** | none |

No mutant timed out. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **integrations/registry.py**: 35 were killed by existing tests not in the first run, mostly `test_integrations_registry_edges.py`, which takes about a minute, so it isn't run for every mutant. The other 10:
  - A capability's version starting at 1.
  - Removing a capability that isn't there not counting as a change, and removing one that is counting.
  - A timeout of exactly 3,600 seconds being accepted, and 3,600.5 refused.
  - Paging defaulting to 100, and a paged output allowing more fields.
  - A preview capability listed as preview only when the caller could use it.
  - An unknown resource option named with the resource it was given to.
  - Three more, recorded with the security notes.
  - → `test_integrations_registry_mutation.py`.
- **opportunities/analyzers.py**:
  - A cell in another language keeping its language, Python that doesn't parse being compared as text, and a cell with only a docstring, `pass` or a comment counting as no code.
  - The run history text: unread, counted with seconds, and counted with none.
  - Two notebooks with the same code: the title naming the kept one and the count, the risk, the cells counted from the first notebook, a copy with one run counting as running, a copy read with no runs not counting, and a copy with unread history named in the validation.
  - The notebook with the most runs being kept, then the most scheduled runs, then the first by name, with unread history ranking like no runs.
  - Notebooks and pipelines whose definitions weren't read being left out.
  - The tables evidence naming what is read and saying when nothing is written, and the other way round.
  - The fingerprint shown being 16 characters.
  - Repeated copies: one opportunity for the same source and sink, worded for it; repeated extraction worded for different sinks; an expression in the source checked first; descriptions and annotations ignored even inside lists; sources that differ inside a list kept apart; a copy with no sink left out; a pipeline with no runs not counted as removed; and a copy inside a larger pipeline making the saving partial.
  - A full refresh saying whether it is a notebook or a pipeline.
  - SQL rewrites numbered from cell 1, and the SQL scan stopping exactly at 200,000 characters.
  - → `test_opportunities_analyzers_mutation.py`.
- **shortcut_center.py**: 6 were killed by `test_shortcut_service_mutation.py` and `test_shortcut_service_paths.py`. The other 28:
  - A storage target's key being its type and location, lower case.
  - A lakehouse you can't read (401 or 403) not being checked.
  - No allowed-connections list blocking no connection.
  - A port in a lakehouse without a schema being found by both table paths.
  - A storage shortcut having no same-environment target.
  - Cross-environment risk text for Prod reading Dev, and for Dev reading Test.
  - An accepted risk recording who, why and when, with blanks for what is missing.
  - Only waiting shortcut requests you can decide being needs, with "Someone" when nobody is named.
  - Every count, exactly.
  - At most 50 readers listed as missing from the target.
  - The CSV cells for a OneLake shortcut, a storage shortcut, a health check change and a change seen at scan.
  - A workspace the platform puts in an environment getting twins whatever its name, a shared target keeping its workspace name, and a missing target twin naming the target's workspace.
  - → `test_shortcut_center_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `opportunities/analyzers.py` | 176 | `same[0]` → `same[1]` for the lakehouse | Equivalent: every notebook in the group has the same default lakehouse. |
| `opportunities/analyzers.py` | 270 | `sort_keys=True` → `False` | Equivalent: `_canon` already builds every dictionary in key order. |
| `opportunities/analyzers.py` | 363 | `m.group(1)` → `m.group(0)` | Equivalent: the pattern around group 1 is only `\b`, which matches no characters. |
| `opportunities/analyzers.py` | 375 | `scheduled=True` → `False` | Equivalent: only NB007 is switched on, and it isn't one of the scheduled-only rules. |

### Real bugs found

None.

### Not in this batch

`integrations/http_server.py` (batch 26).

# Mutation testing, batch 26

Three modules, none touched by an open pull request:
- The local HTTP API: the status each error code gets, the size, rate and concurrency limits at their boundaries, why a request is refused, the OpenAPI document, which resources are listed, the server's settings, `serve()` staying off unless the organization and the person both have the API on, and the parent watcher, on Windows too (`integrations/http_server.py`).
- Before a run starts: ages and durations at each unit boundary, the defaults, each check's evidence and wording, the last five finished runs, CU measurements without a value, input sizes, schedules, and which finding the summary names (`risk.py`).
- Notebook cell dependencies: dotted imports, imports inside nested functions, keyword-only defaults, names read after a function, write calls without a literal name, file-system calls, a cell magic below a line magic, and the line a parse error or a network call is reported on (`depgraph.py`).

Batch 25's modules (`integrations/registry.py`, `opportunities/analyzers.py` and `shortcut_center.py`) were left to batch 25.

The run was at `main` a09aa1fe0 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 25 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_integrations_conformance.py::test_capability_ids_in_docs_are_real` reads `docs/`, so it was left out of the `integrations/http_server.py` run.

`test_risk_service_edges.py` wasn't found by the first search, so `risk.py`'s "before" run was done again with it. It killed no more mutants (101 / 49 both times), since its stand-in service hands the analyzer facts that the other tests already cover.

The new HTTP tests don't open a socket. Requests go through the guard in-process (Starlette's test client, or the guard called directly), and `serve()` runs with a stand-in for uvicorn's server. Like the other new tests, they point `LOCALAPPDATA` and `HOME` at the test's own temporary folder, and they wait on events, not on timing margins.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/http_server.py` | test_integrations_http, test_integrations_http_guard, test_integrations_extensions, test_integrations_audit, test_integrations_peer, test_integrations_resources, test_integrations_conformance, test_cli_local_commands (+ test_integrations_http_mutation) | 150 of 227 | 107 / 43 | **71.3%** | 144 / 6 | **96.0%** | 4 equivalent, 2 not tested |
| `risk.py` | test_risk, test_risk_service_edges, test_compute_check_ast, test_model_bpa (+ test_risk_mutation) | 150 of 257 | 101 / 49 | **67.3%** | 149 / 1 | **99.3%** | 1 equivalent |
| `depgraph.py` | test_depgraph, test_depgraph_edges, test_notebook_magics, test_notebook_resources (+ test_depgraph_mutation) | 150 of 187 | 128 / 22 | **85.3%** | 149 / 1 | **99.3%** | 1 equivalent |

One `http_server.py` mutant timed out before and after, and was counted as killed: it keeps the parent watcher from stopping the server once its parent has gone. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **integrations/http_server.py**:
  - The status of every error code, and the error body saying `"ok": false`.
  - A Content-Length of exactly 1 MiB passing and one byte more being refused, and the same for a streamed body.
  - A result of exactly 1,000,000 bytes being returned and one byte more being refused.
  - A burst of 30 calls per tool, then five a second.
  - Four calls running at once.
  - Refusals saying why: the size limit in KB, no token, a wrong token, and a rate limit that is retryable with a hint and `Retry-After: 1`.
  - The OpenAPI document's error schema, its 503 response, the resource URI's 2,000-character limit, and an invoke body that is optional and takes only `arguments`.
  - Resources that are switched off not being listed.
  - The server's settings: loopback, keep-alive 5 s, 33 connections counting the new one, graceful shutdown 10 s, no access log and no server header.
  - `serve()` staying off when either of the person's two switches, or either of the organization's, is off; and going on to start when all are on.
  - A server that never listened stopping cleanly, with its parent watcher on a daemon thread.
  - A live parent keeping the server running, and no parent pid meaning it follows nobody.
  - A parent that has ended being reported as not alive, and on Windows, only a process that is still active counting as alive, with its handle closed.
  - → `test_integrations_http_mutation.py`.
- **risk.py**:
  - Ages and durations at each unit boundary, and rounding at the next one.
  - The defaults and the worst-first order.
  - A finding's fields, and only a warning being overridable.
  - The policy passing by default, and sign-in needed for Fabric or when asked.
  - A cached capacity reading saying so.
  - The title of a capacity that can't be read, and a paused capacity's status text.
  - An unknown state naming the capacity.
  - The first three definition errors, and an old version naming the environment.
  - Compatibility naming the place the run goes.
  - A running run saying when it started and where, with at most three listed.
  - Only the last five finished runs counting, "1 successful run" being singular, and the failure threshold, including 0 turning it off.
  - CU measurements without a value being skipped, with the evidence, source and threshold wording.
  - Input notes and defaults, and the memory guard only blocking runs on this PC.
  - At most four schedules listed, with the window in minutes.
  - The summary naming the first blocked finding, warnings being counted, acknowledgement following the setting, a non-essential unknown still passing, and the id's length.
  - → `test_risk_mutation.py`.
- **depgraph.py**:
  - `import a.b.c` defining `a`, and an alias or a name from `from m import`.
  - An import inside a nested function staying local to it.
  - Keyword-only defaults being read when the function is defined.
  - Names after a function being read at once, not deferred.
  - `write_table` and file writes needing a literal name or path.
  - Only `sql` calls being read as SQL, and only calls on `fs` changing files.
  - A copy writing its destination.
  - A cell magic one or two lines below a line magic not repeating it.
  - A parse error and a network call being reported on their own lines.
  - A declared dependency carrying no names.
  - → `test_depgraph_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `integrations/http_server.py` | 68 | default status 500 → 501 | Equivalent: an `IntegrationError` can only have a known code, and every known code is in the table. |
| `integrations/http_server.py` | 139 (2) | `[:2]` → `[:3]`, and `("", 0)` → `("", 1)` | Equivalent: a client is a host and a port, and a request with no client is refused either way. |
| `integrations/http_server.py` | 396 | `or` → `and` in the header deadline's close check | Needs a live connection that is part way through a response when the deadline passes; a test of it would depend on timing. |
| `integrations/http_server.py` | 313 | `abandon_on_cancel=True` → `False` | Only changes how soon a call whose client hung up stops waiting for its thread; a test of it would depend on timing. |
| `integrations/http_server.py` | 478 | the parent watcher sleeps 0 s, not 1 s | Equivalent in behaviour: it only checks the parent more often. |
| `risk.py` | 351 | `sort_keys=True` → `False` | Equivalent: the key is a list of strings and tuples, with no dictionary to sort. |
| `depgraph.py` | 257 | `exc.lineno or 1` → `or 0` | Equivalent in practice: `ast.parse` always gives a line number for a syntax error. |

### Real bugs found

None.

### Not in this batch

Batch 25's modules.

# Mutation testing, batch 27

Three modules, none touched by an open pull request:
- Workspace limits for connected tools: the re-check after an approval wait, how deep a record is read, how far a receipt chain is followed, and the resolvers that read a notebook, a proof, an execution and the metadata project (`integrations/scope.py`).
- Go to definition, find references and rename across a notebook: statements on a cell's first line, dotted imports, keyword-only defaults, except and case names, global statements, a method in a class inside a function, how often a name is written in any scope, and the length of a place's line text (`nbsymbols.py`).
- Spark's number formats for to_number and to_char: an empty format, a separator at the end of a digit run, the 38-digit limit, a format that starts with its decimal point, and a sign moved past more than one space (`spark_number_format.py`).

The run was at `main` da9c8c5ac (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 26 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. The `spark_number_format.py` run used `-k "number or to_char or to_varchar"` on the Spark SQL test files, which keeps their 279 number-format tests and leaves out `test_snowflake_sql.py::test_the_docs_table_is_the_codes`, which reads `docs/`. `test_integrations_executions.py` was left out of the `integrations/scope.py` run because it takes about 40 seconds and none of its tests limit a tool's workspaces.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/scope.py` | test_integrations_scope_edges, test_integrations_workspace_scope, test_integrations_permissions, test_integrations_registry_edges, test_integrations_services_mutation (+ test_integrations_scope_mutation) | 150 of 161 | 132 / 18 | **88.0%** | 148 / 2 | **98.7%** | 2 equivalent |
| `nbsymbols.py` | test_nbsymbols (+ test_nbsymbols_mutation) | 150 of 268 | 117 / 33 | **78.0%** | 148 / 2 | **98.7%** | 2 equivalent |
| `spark_number_format.py` | test_spark_number_format_contract, test_spark_builtins_sql, test_spark_builtins_sql_edges, test_spark_builtins_sql_mutation, test_snowflake_sql, with the `-k` filter above (+ test_spark_number_format_mutation) | 150 of 272 | 139 / 11 | **92.7%** | 144 / 6 | **96.0%** | 6 equivalent |

Three `spark_number_format.py` mutants timed out before and after, and were counted as killed. The three new test files pass on DuckDB 1.5.6 and 2.0.0. The number-format tests go through `sql.run`, as the other Spark SQL tests do.

### What the survivors were, and the tests that now kill them

- **integrations/scope.py**:
  - The re-check after an approval wait: a call that reaches only the tool's workspaces passing, one that reaches another workspace or a place it can't tell being refused with the reason and where to change it, and a tool with no limit reaching anything.
  - A workspace id 12 levels deep being found, one 13 levels deep making the record impossible to check, counted the same through dictionaries and lists, and every part read from the top.
  - A receipt chain of three being followed to its proof, and a longer one being impossible to check.
  - A notebook reaching both its lakehouse's workspace and the one it publishes to, with the call's own workspace added.
  - A record whose lakehouse has no workspace id being impossible to check.
  - An execution with no call context being read for no client.
  - The metadata project reaching the workspaces its entities name.
  - → `test_integrations_scope_mutation.py`.
- **nbsymbols.py**:
  - A def or a global statement on a cell's first line.
  - `import os.path` binding `os`, and shadowing the module's `os` inside a function.
  - Keyword-only defaults of a function and of a lambda being read where they are written.
  - An except name at module level being a definition, with another except name left alone.
  - Case captures and stars being definitions, found where the pattern binds them.
  - A global statement finding the name after its keyword, with the name earlier on the line skipped.
  - A method in a class inside a function seeing the module's name, past its class's own.
  - How often a name is written in any scope counting only that name: arguments, classes, defs, except names and the rest of a mapping pattern.
  - A place's line text being cut at 200 characters.
  - → `test_nbsymbols_mutation.py`.
- **spark_number_format.py**:
  - An empty format saying it is empty.
  - A separator at the end of a digit run being refused.
  - 38 digits being the most a format can have, with or without a decimal point.
  - A format that starts with its decimal point reading the fraction.
  - A sign moving left past every space before it.
  - → `test_spark_number_format_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `integrations/scope.py` | 135, 141 | `return False` → `return None` | Equivalent: both are false inside `all()`. |
| `nbsymbols.py` | 27 | `[0, 0]` → `[1, 0]` | Equivalent: lines count from 1, so the first entry is never read. |
| `nbsymbols.py` | 221 | `+ 6` → `+ 7` after `global` | Equivalent: `global` is always followed by at least one space, so the name starts at 7 or later. |
| `spark_number_format.py` | 19 | `frozen=True` → `False` | Equivalent: a digit run is never changed or hashed. |
| `spark_number_format.py` | 90 | `-1` → `-0` for no dollar sign | Equivalent: the checks only compare it with positions, which are never negative. |
| `spark_number_format.py` | 99 | `seps[j - 1]` → `seps[j + 1]` | Equivalent: two separators side by side are still caught at the first one, and a leading one by `j == 0`. |
| `spark_number_format.py` | 233 | `else 0` → `else 1` digits after the point | Equivalent: with no decimal point, nothing reads the digits after it. |
| `spark_number_format.py` | 297, 298 | `or` → `and`, and the `"0"` fallback dropped | Not reached: every valid format of up to four parts gives text other than a lone sign. |

### Real bugs found

None.

### Not in this batch

Batch 26's modules.

# Mutation testing, batch 28

Three modules, none touched by an open pull request:
- Will this SQL run on another platform: the size limit at its edge, findings that can't be changed, QUALIFY's ORDER BY when a key names an output column (plain, qualified by the same table or another one, or an alias over another column), the hidden columns it adds otherwise, and Snowflake SQL that can't be rewritten for DuckDB but still goes through sqlglot for another platform (`sql_portability.py`).
- Spark's decimal result types: TINYINT, SMALLINT and the literal 0 as operands, a DECIMAL without its precision, a sum of an integer or a double next to an expression that changes, the names of unnamed columns before and after a star, a statement that isn't a query, an average that stays exact up to its limit, and the DataFrame API's alias (`spark_decimals.py`).
- Databricks VARIANT on this PC: where a number stops being a BIGINT or a DECIMAL, integer casts at the edges of their ranges, how much of a value an error repeats, a space in a string, nesting counted outside strings, an empty path, and a decimal type without a scale (`databricks_variant.py`).

Batch 27's modules (`integrations/scope.py`, `nbsymbols.py` and `spark_number_format.py`) were left to batch 27, which ran at the same time.

The run was at `main` 91ab7b3f6 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 27 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Three tests in `test_databricks_coverage.py` read `docs/`, so they were left out of the `databricks_variant.py` run: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_portability.py` | test_sql_portability, test_kudp_guardrails, test_snowflake_sql_variadic (+ test_sql_portability_mutation) | 145 of 145 | 127 / 18 | **87.6%** | 142 / 3 | **97.9%** | 3 equivalent |
| `spark_decimals.py` | test_spark_decimal_types, test_spark_dataframe_decimals, test_shell_and_decimals_edges (+ test_spark_decimals_mutation) | 150 of 237 | 129 / 21 | **86.0%** | 146 / 4 | **97.3%** | 4 equivalent |
| `databricks_variant.py` | test_databricks_variant_edges, test_databricks_coverage (+ test_databricks_variant_mutation) | 150 of 241 | 122 / 28 | **81.3%** | 143 / 7 | **95.3%** | 7 equivalent |

The three new test files pass on DuckDB 1.5.6. Two real bugs were found (below), and each has a strict xfail that a fix will turn into a pass.

### What the survivors were, and the tests that now kill them

- **sql_portability.py**:
  - SQL of exactly 200,000 characters being checked and one character more being refused, with the message.
  - A finding that can't be changed.
  - An ORDER BY key that names an output column being used as it is: plain, in another case, or qualified by the same table.
  - A key that names another table's column, or a table column that shares its name with an output alias, being hidden in the subquery as `_o1` and not returned.
  - Each hidden key getting its own name, in order.
  - Snowflake SQL that DuckDB can't take still being rewritten by sqlglot for Spark and PostgreSQL, with sqlglot's reason.
  - → `test_sql_portability_mutation.py`.
- **spark_decimals.py**:
  - A TINYINT operand being decimal(3,0), a SMALLINT decimal(5,0), the literal 0 decimal(1,0), and `00.5` decimal(1,1).
  - A DECIMAL without its precision leaving only that expression as it was.
  - A sum of an integer or a double keeping DuckDB's type next to an expression that changes.
  - Unnamed columns, without a star and after one, keeping DuckDB's names.
  - An INSERT ... VALUES still being rewritten.
  - An average of a 34-digit decimal staying exact, not going through DOUBLE.
  - A DataFrame expression's alias being kept apart from its SQL.
  - → `test_spark_decimals_mutation.py`.
- **databricks_variant.py**:
  - An error repeating at most 100 characters of a value.
  - 2^63 being a decimal, so a BIGINT cast stops by name, while the smallest and largest BIGINT cast.
  - TINYINT, SMALLINT and INT casts holding exactly their ranges.
  - A decimal having at most scale 38 and 38 digits, so one more of either makes it a DOUBLE.
  - A space in a string being kept, not escaped.
  - Nesting being counted outside strings, after an escaped quote or backslash, and not across siblings.
  - An empty path not being a path.
  - A decimal type without a scale stopping by name.
  - A double that prints without an exponent casting to a decimal.
  - → `test_databricks_variant_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `sql_portability.py` | 137 (2) | no QUALIFY: `return False` dropped, or → `True` | Equivalent: it's only called for a SELECT that has QUALIFY. |
| `sql_portability.py` | 179 | `return` dropped for a rewrite that isn't a SELECT | Equivalent: sqlglot's `eliminate_qualify` gives a SELECT back for a SELECT. |
| `spark_decimals.py` | 122 | `scale + 1` → `scale` in the division's unit | Equivalent: the unit is below 1, so DECIMAL(scale, scale) holds it too, and the scale is at least 4 here. |
| `spark_decimals.py` | 219 | `return unchanged` dropped when the root isn't a SELECT | Equivalent: the rewrite never replaces the root SELECT. |
| `spark_decimals.py` | 244, 245 | `quote_identifiers` and `identify` → `True` | Equivalent: the qualified copy is only read for types. |
| `databricks_variant.py` | 35 | BIGINT's 64 bits → 65 | Equivalent: a JSON integer past 2^63 is a decimal, so a BIGINT cast never sees it. |
| `databricks_variant.py` | 57 | `copysign(1, x) < 0` → `< 1` | Equivalent: `copysign(1, x)` is 1 or -1. |
| `databricks_variant.py` | 77 | 20 characters → 21 for a BIGINT | Equivalent: JSON has no leading zeros, so 21 characters is always past 2^63. |
| `databricks_variant.py` | 82 | `max(-exponent, 0)` → `max(-exponent, 1)` | Equivalent: the scale is only compared with 38. |
| `databricks_variant.py` | 238 | `_at`'s missing path → found | Equivalent: a missing path and a VARIANT null both give NULL. |
| `databricks_variant.py` | 293 | `and` → `or` in the double check | Equivalent: a VARIANT is read back from its text, where a double without an exponent reads as a decimal, so a double always has one. |
| `databricks_variant.py` | 322 | `side_effects=False` → `True` | Equivalent in results: it only stops DuckDB from folding a call with constant arguments. |

### Real bugs found

- `databricks_variant.parse_json` rounds a decimal with more than 28 digits, because `Decimal.normalize()` uses the default 28-digit context. For example, `1111111111111111111111111111111111111.5` comes back as `1111111111111111111111111111000000000`. Filed as #2662, and `test_a_decimal_keeps_all_38_digits` is a strict xfail for it. (The 39-digit mutant is still killed, because a 39-digit number prints as a DOUBLE.)
- Spark's decimal result types are skipped for a table or column whose name has a capital letter, in Spark SQL and in the DataFrame API: `SELECT Amt / b FROM t` gives DOUBLE, not DECIMAL(23,13). The schema keeps names as written (`normalize=False`), while Spark's `qualify` lower-cases the query. Filed as #2663, with two strict xfails. They also kill the two `normalize=False` → `True` mutants, which fix it.

### Not in this batch

Batch 27's modules.

# Mutation testing, batch 29

Three modules, none touched by an open pull request:
- App Studio's patch operations, the one way a specification changes: the 200-operation limit, field paths, the inverses that undo each operation, the refusals and the names cut short in their messages (`appstudio/patch.py`).
- Meaningful diffs of a table's settings and of a notebook: how values are written, which changes are logic, the order changes are listed in, list items matched by identity, and a notebook cell's position and diff (`meta/diff.py`).
- Schema drift review: what a run expects from its source, the added, dropped and retyped columns, the config edits that settle them, Delta's type-widening rules and the schema-change kinds a write reports (`meta/drift.py`).

The run was at `main` aa6b5668a (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 28 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_meta.py` was left out of the `meta/drift.py` run because it takes about 40 seconds a mutant and only one of its tests mentions drift.

None of these modules writes files, and the new tests use stand-in entities, no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/patch.py` | test_appstudio, test_appstudio_patch_edges, test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_pbir (+ test_appstudio_patch_mutation) | 135 of 135 | 113 / 22 | **83.7%** | 132 / 3 | **97.8%** | 3 equivalent |
| `meta/diff.py` | test_meaningful_diffs, test_dataops_edges (+ test_meta_diff_mutation) | 134 of 134 | 108 / 26 | **80.6%** | 133 / 1 | **99.3%** | 1 equivalent |
| `meta/drift.py` | test_meta_drift_edges, test_schema_evolution, test_dataops, test_dataops_edges (+ test_meta_drift_mutation) | 150 of 214 | 122 / 28 | **81.3%** | 143 / 7 | **95.3%** | 7 equivalent |

One `meta/drift.py` mutant timed out before and after, and was counted as killed. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **appstudio/patch.py**:
  - A change of exactly 200 operations being applied, and one more refused.
  - A field that is empty or not text needing a field, and a page setting its own fields.
  - An id already in use being refused by the operation itself, before the whole specification is checked.
  - The inverse running last operation first, and each touched id listed once.
  - A set recording the old value, or none for a field that wasn't there, and a value copied rather than shared.
  - A page whose components are only used inside it being removable, and one whose component is used from outside not.
  - A move to another page, within a page and within a list, each with its inverse, and a move to a bad index putting the entry back.
  - Long operation names, field paths, page ids and ids cut at 20, 80 and 64 characters in messages.
  - → `test_appstudio_patch_mutation.py`.
- **meta/diff.py**:
  - Values written the way the file writes them: booleans in lower case, lists in brackets.
  - A contract with no keys reading as an empty list, and keys or columns not repeated as settings.
  - A setting set, removed or changed, nested settings by their full path, and a table replaced by a value.
  - Operational settings not being logic changes, only in their own section.
  - Logic changes listed before descriptive ones, and column changes in column order.
  - List items matched by name, column, python and expression, by table and business key, by lookup name or dimension, and a dedupe step, with numbered items for ones without an identity.
  - A reorder reported only when nothing else changed.
  - Entities in name order, unchanged ones left out, and unreadable ones said to be unreadable.
  - A notebook cell's id or position, its joined source, its one-line context diff, its old type, and a reorder reported once.
  - → `test_meta_diff_mutation.py`.
- **meta/drift.py**:
  - The source named by its entity, then its files, then its table, and the layer's policy used when the table has none.
  - A step, or a table without a source, having no differences.
  - Added, dropped and retyped columns, a dropped key flagged, a renamed column read by its contract name, and a column a derived expression reads not being new.
  - Reading from another entity only reporting a missing column this entity uses.
  - Section bounds, columns removed, retyped and added in place, comments kept, and a policy or rename replacing the old line.
  - Widening for unsigned integers to decimals and doubles, and 16-bit integers to decimals.
  - Every kind of schema change listed once, in order, with its breaking flag.
  - → `test_meta_drift_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `appstudio/patch.py` | 36 | `entry_kind == "page"` → `!=` | Equivalent: `components` is already refused for every kind by the first test. |
| `appstudio/patch.py` | 97 | `[:64]` → `[:65]` for an id already in use | Equivalent: an id in use passed validation, so it is never longer than 64 characters. |
| `appstudio/patch.py` | 144 | `or` → `and` | Equivalent: the only entry with no list is the application, whose kind isn't in the list either. |
| `meta/diff.py` | 92 | sort default `0` → `1` | Equivalent: only changes without a column use the default, and they all get the same one. |
| `meta/drift.py` | 30 | `base_type()` used the other way round | Equivalent: a Polars type and its instance find the same entry. |
| `meta/drift.py` | 107 | the new columns section's start moved by one | Equivalent: the lines written are the same. |
| `meta/drift.py` | 128, 140 | a new section's end moved by one | Equivalent: an empty slice at either end inserts at the same place. |
| `meta/drift.py` | 186, 188 | `or 38` → `or 39` | Equivalent: Polars gives a decimal with no precision 38, so the fallback is never read. |

### Real bugs found

None.

### Not in this batch

Batch 28's modules.

# Mutation testing, batch 30

Three modules, none touched by an open pull request:
- Spark's `hash()` and `xxhash64()` on this PC: the result types (INTEGER and BIGINT, as in Spark), the same call always building the same SQL, builds at the same time each keeping their own lambda names, and the seed for a NULL (`spark_hash.py`).
- The known differences between a pipeline that passes here and one Fabric accepts: a notebook timeout one second over 12 hours, the parameter a global-parameter warning names, where a Key Vault secret is read, a Key Vault call by an expression URL, gotchas being fixed once made, the containers walk ending at the top, at a missing parent or after 100 steps, and a Set Variable in a sequential ForEach inside a parallel one (`orchestration/gotchas.py`).
- The App Studio specification: every text and count limit at and one past its value, required texts, control characters, the twelve-column grid's edges, `Table[Column]` fields, each target's own components and interactions, references by kind, an early problem not stopping the later checks, writeback for an unknown target, and `find`, `page_of`, `dependencies`, `dependents` and `copy_of` (`appstudio/spec.py`).

Batch 29's modules (`appstudio/patch.py`, `meta/diff.py` and `meta/drift.py`) were left to batch 29, which ran at the same time.

The run was at `main` 21897a615 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 29 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_spark_coverage.py` reads `docs/`, so it was left out of the `spark_hash.py` run.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_hash.py` | test_spark_hash, test_spark_hash_refused (+ test_spark_hash_mutation) | 150 of 216 | 114 / 36 | **76.0%** | 115 / 35 | **76.7%** | 35 equivalent |
| `orchestration/gotchas.py` | test_pipeline_gotchas, test_notebook_formats (+ test_pipeline_gotchas_mutation) | 125 of 125 | 115 / 10 | **92.0%** | 125 / 0 | **100%** | none |
| `appstudio/spec.py` | test_appstudio, test_appstudio_spec_edges, test_appstudio_generate(_edges), test_appstudio_edit(_edges), test_appstudio_pbir(_edges) (+ test_appstudio_spec_mutation) | 150 of 168 | 122 / 28 | **81.3%** | 150 / 0 | **100%** | none |

`spark_hash.py` was already checked value for value against Apache Spark 3.5.3. Its survivors are almost all fields a step never reads (below), so its score stays low while nothing is left untested. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_hash.py**:
  - `hash()` being cast to INTEGER, not BIGINT, so a mutant that changed 32 to 33 in the width check gave the right numbers as the wrong type.
  - → `test_spark_hash_mutation.py`.
- **orchestration/gotchas.py**:
  - The 12-hour notebook limit at 43,200 seconds, so 12:00:01 is warned about and 12:00:00 isn't.
  - The global-parameter warning naming the parameter, not the whole match.
  - A Key Vault warning's path pointing at the secret reference or at the expression that reads it.
  - `Gotcha` being frozen.
  - The containers walk stopping after 100 steps on a definition that contains itself, and at the top or a missing parent.
  - → `test_pipeline_gotchas_mutation.py`.
- **appstudio/spec.py**:
  - Each limit at and one past its value: name 120, purpose 2,000, audience entries 1 to 120, source texts 256, measure name 128 and expression 8,000, titles 120 and 200, font 80, a colour key cut at 40, 50 pages and 400 components.
  - Required texts that are missing, not just empty.
  - The grid: `x + w` at 12 allowed and at 13 refused, and `w` or `h` of 0 refused.
  - A problem found before the ids are checked not stopping the rest, while a collection that isn't a list of objects does.
  - Components that aren't objects being refused by page.
  - An unknown target having no writeback.
  - → `test_appstudio_spec_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_hash.py` | 131–210, 305 (24) | `s` 0 → 1 in a `set`, `add` or `mul` step | Equivalent: those steps read only `c`, never `s`. |
| `spark_hash.py` | 54 | six registers → seven | Equivalent: the seventh is never read. |
| `spark_hash.py` | 57 (2) | `_op`'s default `s` or `c` 0 → 1 | Equivalent: `_ops` always passes all four fields. |
| `spark_hash.py` | 76 | three steps → four before steps are packed as text | Equivalent: both ways give the same steps. |
| `spark_hash.py` | 82 | a missing `c` padded with 1 | Equivalent: only steps that don't read `c` leave it out. |
| `spark_hash.py` | 89 | one part → none before `LIST_CONCAT` | Equivalent: `LIST_CONCAT` of one list is that list. |
| `spark_hash.py` | 193 | the 64-bit mask `(1 << 64) - 1` → `(0 << 64) - 1` | Equivalent: the step adds modulo 2^64, so `-P1` and its 64-bit form give the same lane. |
| `spark_hash.py` | 237 (4) | a float's sign wrap at 2^31 or 2^32 changed | Equivalent: both hashes read a float's bits as 32 bits (masked), so the wrap doesn't change them. `-0.1` is in the Spark fixture. |

### Not in this batch

Batch 29's modules.

# Mutation testing, batch 31

Three modules, none touched by an open pull request:
- Warehouse compare and deploy: where a built-in type's name (`Date`, `Count`) folds and where it stays a column's name, quoted names with doubled quotes, the side-by-side view's 3,000-row cut and changed blocks of uneven length, changed rows listed first, the order a deploy runs its steps in, the one-definition check for views, functions, tables and schemas, and the preview's precondition (`warehouse_compare.py`).
- Reading pipeline expressions: whole values and interpolations at the start of a string, a real `@{` right after an escaped `@@{`, quotes inside an interpolation, a value typed as something other than `Expression`, member chains with `?.`, `[..]` and a trailing dot, calls with nothing after them, and `pipeline().parameters` with no name (`orchestration/expressions.py`).
- T-SQL's `FORMAT` on the local engine: backslashes inside and outside quoted text, a `%` that isn't before a letter, and the digits and places a number format may have (`localdata/tsql_format.py`).

Batch 30's modules (`spark_hash.py`, `orchestration/gotchas.py` and `appstudio/spec.py`) were left to batch 30, which ran at the same time.

The run was at `main` 46d790e27 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 30 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Three tests in `test_tsql_loud_gaps.py` read `docs/` or the module's source, so they were left out of the `tsql_format.py` run.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse_compare.py` | test_warehouse_compare_more_edges, test_warehouse_precondition, test_warehouse_project (+ test_warehouse_compare_mutation) | 150 of 333 | 121 / 29 | **80.7%** | 147 / 3 | **98.0%** | 3 equivalent |
| `orchestration/expressions.py` | test_orchestration_expressions_edges, test_pipeline_definitions, test_pipeline_expression_numbers, test_pipeline_gotchas, test_orchestration_definition_edges, test_orchestration_definition_mutation (+ test_orchestration_expressions_mutation) | 150 of 251 | 116 / 34 | **77.3%** | 143 / 7 | **95.3%** | 7 equivalent |
| `localdata/tsql_format.py` | test_tsql_loud_gaps, test_local_tsql, test_local_tsql_more_edges, test_sql_editor (+ test_tsql_format_mutation) | 150 of 206 | 127 / 23 | **84.7%** | 141 / 9 | **94.0%** | 9 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **warehouse_compare.py**:
  - A built-in type's name after `AS` folding only before `)`, and after `(` only before `,`, so an alias or a column called `Date` keeps its case. A soft word second in the script, or last.
  - `"a""b"` and `[a]]b]` read as one name each.
  - The side-by-side view cut at 3,000 rows, and a changed block of two lines against one (or one against two) leaving a side empty.
  - Changed rows before rows only in the project, whatever their names.
  - Each deploy step's place in the order (schemas, tables, functions, views, procedures, then drops in reverse), with names chosen so that sorting by name would give another order.
  - The one-definition check: a view is cut at its first `;` even when its text has the word `begin`, a function without a body likewise, a table or schema file has to start with its own `CREATE`, a schema may have only `AUTHORIZATION` and its owner after its name, and a table needs a column list.
  - The preview's precondition having columns only for a table that exists.
  - Why a step is offered or not: "new", "new table, empty", "definition changes", and an alter that isn't one definition.
  - Three more, recorded with the security notes.
  - → `test_warehouse_compare_mutation.py`.
- **orchestration/expressions.py**:
  - `@concat(...)` not interpolated, and `@{...}` at the very start of a string read.
  - `x@@{@{b}}` reading `b`; `@{'}'}` keeping its quoted brace; a quote closing before the next interpolation; a quote at the very end of an open one.
  - Only a value typed `Expression` read as one, an `Expression` value starting with `@{` read as its parts, and a whole one not interpolated.
  - A doubled quote in a string read as one quote.
  - Member chains: `?.output?['rows']`, `['rows'][0].x`, `['x'].y`, and chains that stop at `.`, `?` or `[`.
  - `activity('A')` and `item()` with nothing after them, `utcnow().x` giving no reference, and `pipeline().parameters` with no name read as a system reference.
  - → `test_orchestration_expressions_mutation.py`.
- **localdata/tsql_format.py**:
  - `'a\'b'` in a date format giving `a'b`, and quoted text that ends in a backslash refused as not closed.
  - `\d` outside quotes keeping one character, and a backslash at the end refused.
  - `d%` and `%%d` refused.
  - `N123`, `Nx`, `N2x` and `F100` refused, `N39` and `F99` refused for more than 38 places, and `F37` run.
  - → `test_tsql_format_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `warehouse_compare.py` | 66 | `t[:2]` → `t[:3]` in the `N'...'` check | Equivalent: a plain `'` string is caught first, and an `N'...'` token is never a keyword, so it doesn't fold either way. |
| `warehouse_compare.py` | 67 | a string marked as a word | Equivalent: a quoted string is never in `RESERVED` or `SOFT`, so it doesn't fold. |
| `warehouse_compare.py` | 322 | the default order 10 → 11 | Equivalent: only a changed schema uses the default, so nothing ties with it. |
| `orchestration/expressions.py` | 191, 194, 197 (4) | `toks[i][0]` → `toks[i][1]` | Equivalent: a punctuation token's kind and value are the same text. |
| `orchestration/expressions.py` | 223, 229 (2) | `toks[i + 3][0]` or `arg[0]` → `[1]` | Equivalent: the same, for `)`. |
| `orchestration/expressions.py` | 250 | `references()` reading as interpolated | Equivalent: it returns only the references, which don't depend on that flag. |
| `localdata/tsql_format.py` | 22 | `culture` marked required | Equivalent: `build` makes the node directly, so sqlglot never checks its arguments. |
| `localdata/tsql_format.py` | 159 | a run counted from 0 | Equivalent: the loop counts the first letter itself, so the run's length is the same. |
| `localdata/tsql_format.py` | 177 | `n == 1` → `n == 0` for `y` | Equivalent: padding one digit to a width of one leaves it as it is. |
| `localdata/tsql_format.py` | 192 | the empty-format return dropped | Equivalent: unreachable, since an empty format is refused first and every format character adds a part. |
| `localdata/tsql_format.py` | 195, 227, 239, 243, 244 (5) | `||` made NULL-safe | Equivalent: DuckDB writes it as `||` either way, and the value is never NULL there (a NULL is answered before). |

### Not in this batch

Batch 30's modules.

# Mutation testing, batch 32

Three modules, none touched by an open pull request:
- The SQL compatibility check's reader: the 500,000-character limit, the report listing the first 1,000 findings while counting every one, SQL that can't be tokenized, T-SQL's `GO` alone on its line (and not in Spark SQL), each word's place in a two-word token such as `ORDER BY`, sqlglot's error text without its token dump, and the exported JSON's indent and characters (`sqlcompat/analyzer.py`).
- Validate notebook's code checks: a finding's cell id, markdown and blank cells not counted as code, a reason with no cell said about the notebook, the missing lakehouse said once, package severity by where the notebook runs, an import used only inside a function, the second parameter cell, frozen cells, the lakehouse's name, a lakehouse file read with no lakehouse, the first Spark cell stopping a run without Spark, a run failure making its warning an error, sort order, and the saved report (`validate.py`).
- Checks on a pipeline definition: duplicate JSON keys (up to ten listed), a `Pipeline` passed in directly, the severity order, how an inactive activity is marked, a missing type, which activity a dependency loop is reported on, an upstream walk past a dependency that doesn't exist, Set variable and the pipeline's return value, the activity's own JSON, `item()` outside a ForEach, and the summary's counts (`orchestration/validate.py`).

Batch 31's modules (`warehouse_compare.py`, `orchestration/expressions.py` and `localdata/tsql_format.py`) were left to batch 31, which ran at the same time.

The run was at `main` ed38d4cf5 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 31 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_practice_docs.py` and `test_the_shipped_guides_are_the_docs` read `docs/`, so they were left out of the `sqlcompat/analyzer.py` run.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sqlcompat/analyzer.py` | the 12 other test files that use `sqlcompat` (+ test_sqlcompat_analyzer_mutation) | 136 of 136 | 110 / 26 | **80.9%** | 129 / 7 | **94.9%** | 6 equivalent, 1 unreachable (#2674) |
| `validate.py` | test_validate, test_validate_service_edges, test_compute_check_ast, test_spark_refusals (+ test_validate_mutation) | 150 of 163 | 115 / 35 | **76.7%** | 149 / 1 | **99.3%** | 1 equivalent |
| `orchestration/validate.py` | test_opportunities_analyzers_mutation, test_orchestration_definition_edges, test_pipeline_definitions (+ test_orchestration_validate_mutation) | 133 of 133 | 102 / 31 | **76.7%** | 128 / 5 | **96.2%** | 5 equivalent |

Two real bugs were found, both in `sqlcompat/analyzer.py`. Each is filed with a strict xfail in `test_sqlcompat_analyzer_mutation.py`, so a fix flips it:
- #2673: in T-SQL, a name spelled `go` splits the statement (`SELECT x AS go FROM t` is read as two statements).
- #2674: a statement that doesn't parse is reported at its first token, not at the token where parsing failed. The supported sqlglot versions don't put `start` in their error dicts, so `error_at` is never set.

### What the survivors were, and the tests that now kill them

- **sqlcompat/analyzer.py**:
  - SQL of 500,001 characters not checked (with the limit in the text, and `truncated` false), and 500,000 checked.
  - 1,001 findings listing 1,000 with `truncated` true while the counts say 1,001, and 1,000 not truncated.
  - SQL that can't be tokenized saying why, with its finding at offset 0 to 1 in statement 0.
  - `GO` alone on its line, in either case and with spaces around it, splitting T-SQL. Not splitting Spark SQL. Not alone when other text is on its line, at the start or at the end of the query.
  - `ORDER` and `BY` keeping their own offsets.
  - sqlglot's description kept as the error, with the token dump dropped.
  - The exported JSON indented by two and keeping `é` as written.
  - → `test_sqlcompat_analyzer_mutation.py`.
- **validate.py**:
  - A finding's cell id taken only when it has a cell.
  - A notebook of markdown and blank cells being empty.
  - The missing lakehouse not repeated as a Fabric reason, a reason with a cell offering the fix, and a reason with no cell titled "The notebook" with no fix.
  - `package_missing_pc` and `package_unknown_fabric` severities for each Runs on, the PC's version said when it's installed, and an import used only inside a function giving no finding.
  - The second parameter cell named.
  - "cell" and "cells" for frozen cells.
  - The lakehouse's name, or "attached".
  - `Files/` read with no lakehouse being an error.
  - The first Spark cell, by number and id, stopping a run without Spark.
  - A NameError in the clean run making the matching warning an error found by both.
  - Findings with no cell first, six names then "and 1 more", an unknown effect cut at its first `;`.
  - The saved report: the revision cut to 16 characters, only sections with findings, a fix only when there is one, and an empty cell for unknown seconds.
  - → `test_validate_mutation.py`.
- **orchestration/validate.py**:
  - Eleven duplicate keys listing ten, no warning without duplicates, and a `Pipeline` from `build` validated directly.
  - Errors before warnings before info when they're found in another order.
  - An inactive activity marked Succeeded, or as `onInactiveMarkAs` says.
  - "A has no type." with its activity, or "An activity has no type." with none.
  - A loop reported on its first activity, by path and message, and a loop reached through another activity.
  - The upstream walk not failing on a dependency that doesn't exist.
  - A variable name that isn't text being missing, and `pipelineReturnValue` needing `setSystemVariable`.
  - The activity's own JSON keeping its other keys and a Switch's cases without their activities. Expressions outside `typeProperties` checked.
  - `item()` on a ForEach's own items, and inside an If that isn't in a ForEach.
  - The summary counting each severity, an unknown one included.
  - → `test_orchestration_validate_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `sqlcompat/analyzer.py` | 92 (2) | `t.token_type == VAR` or `t.text.upper() == "GO"` swapped | Equivalent: sqlglot's T-SQL tokenizer makes every `go` a `COMMAND` token, so the `VAR` branch never sees one, and a `go` alone on its line is split by the first branch anyway. |
| `sqlcompat/analyzer.py` | 124 | `rfind` from 1 instead of 0 | Equivalent: it only misses a newline at offset 0, and the line is stripped before it's compared. |
| `sqlcompat/analyzer.py` | 125 (2) | `find` from `t.end - 1` or `t.end` | Equivalent: both offsets are inside the `GO` token, which has no newline. |
| `sqlcompat/analyzer.py` | 163 | `stmt.start + start` → `-` | Unreachable: the supported sqlglot versions' errors have no `start` (#2674). |
| `sqlcompat/analyzer.py` | 184 | the statement count 0 → 1 in the too-long outcome | Equivalent: with a reason, `_outcome` returns it without reading the count. |
| `validate.py` | 71 | `parsed` defaulting to False | Equivalent: depgraph always sets `parsed`. |
| `orchestration/validate.py` | 63 | info's rank 2 → 3 | Equivalent: it stays after warning's 1, and no other severity is used. |
| `orchestration/validate.py` | 64 | the default rank 3 → 4 | Equivalent: every diagnostic has a known severity. |
| `orchestration/validate.py` | 168 | a finished node's state 2 → 3 | Equivalent: only state 1 is compared. |
| `orchestration/validate.py` | 264, 270 (2) | `return False` → `return None` in `_in_foreach` | Equivalent: the only caller uses it in `not ...`. |

### Not in this batch

Batch 31's modules.

# Mutation testing, batch 34

Three modules, none touched by an open pull request, each with tests that run in a few seconds:
- `spark.sql` parameters: the 32-bit edge between INT and BIGINT literals, NaN and the infinities as DOUBLE, a Decimal Spark's DECIMAL can't hold, `?` inside a string, which colons are markers, one DataFrame used twice in `{}` fields, and `getArgument` calls that are cut short or in SQL that can't be read (`spark_sql_params.py`).
- Column lineage: derive and lookup entries without their column, a derive that reads its own name, an expression that can't be read, mode and Type 2 history columns added once, a lookup's dimension key, and upstream walks past a missing entity or round a cycle (`meta/lineage.py`).
- The SQL compatibility profiles: which targets run on this PC, and each documentation link's last-updated date (`sqlcompat/profiles.py`).

Batch 33's modules were left to batch 33, which ran at the same time.

The run was at `main` fd8230b51 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 33 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways (369 passed), so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

None of these modules writes files, and the new tests use no network and no timing. The lineage tests use stand-in entities, so no project is created on disk.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_sql_params.py` | test_spark_sql_params, test_sql_cell_widgets (+ test_spark_sql_params_mutation) | 150 of 169 | 128 / 22 | **85.3%** | 148 / 2 | **98.7%** | 2 equivalent |
| `meta/lineage.py` | test_lineage, test_meta_service_edges (+ test_meta_lineage_mutation) | 96 of 96 | 85 / 11 | **88.5%** | 96 / 0 | **100%** | none |
| `sqlcompat/profiles.py` | test_learn_fixes_edges, test_sql_compat (+ test_sqlcompat_profiles_mutation) | 15 of 15 | 11 / 4 | **73.3%** | 15 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_sql_params.py**:
  - 2,147,483,647 and -2,147,483,648 written as INT, and one past either end as BIGINT (`L`).
  - NaN, Infinity and -Infinity written as DOUBLE casts, each with its own text.
  - Decimal NaN and the infinities refused, naming DECIMAL.
  - `'?'` in a string not counted as a positional marker.
  - `(x)`, a colon at the end of the query and `a:b` not read as markers, and `:a` at the very start bound.
  - One DataFrame in two `{}` fields registered once under one name, and a second DataFrame under another.
  - `getArgument` in SQL that can't be tokenized left as it is, and calls cut short (no parenthesis, no closing parenthesis after the name or after the default) left alone.
  - Another function called on a string not filled as if it were `getArgument`.
  - → `test_spark_sql_params_mutation.py`.
- **meta/lineage.py**:
  - Derive and lookup entries with no `column` or `as`, and entries that aren't tables, skipped.
  - A derive of `a` from `a * 2` reading the source's `a`, with no note.
  - An expression that can't be read giving no inputs and its note.
  - Mode columns added once, after the contract's columns.
  - Type 2 history columns only on a Type 2 entity, each once.
  - A lookup naming the dimension's key only when the dimension exists and has one.
  - Upstream of an entity the project doesn't have being empty, and upstream stopping at a cycle.
  - → `test_meta_lineage_mutation.py`.
- **sqlcompat/profiles.py**:
  - Only Spark SQL and DuckDB run on this PC.
  - Each documentation link carrying its last-updated date, not its URL.
  - → `test_sqlcompat_profiles_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_sql_params.py` | 43 | `value > 0` → `value > 1` | Equivalent: the value there is always an infinity, so both comparisons agree. |
| `spark_sql_params.py` | 103 | `zip(..., strict=True)` → `strict=False` | Equivalent: the lengths are checked just before, so the zip never sees a difference. |

### Not in this batch

Batch 33's modules.

# Mutation testing, batch 33

Three modules, none touched by an open pull request:
- Line magics and Spark SQL in notebook cells: a triple-quoted string with text after it on its line, a bracket or escaped quote inside a string on the line before a magic, extra spaces between a magic's words, which statement decides whether SQL returns rows, a quoted name before the verb, a backslash inside quotes and backticks, and what follows a block comment (`magics.py`).
- Cost and capacity figures: each F and P SKU's size, which saved settings are taken, a time with an offset, a run under a second, the CUs of each engine, the reasons given when a figure isn't known, the price note, the report's rounding and order, and the share of a capacity (`costs.py`).
- Kinds of repository and where notebooks find their data: the checked kind, the message when a stated kind would change, an unreadable or readable `locations.toml`, `runs_on = "this_pc"` winning over a workspace, and the 256-character limit on a location's fields (`git/scopes.py`).

The run was at `main` b76c42d29 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 32 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways (509 passed, and the tests added after that passed the same way), so no run below can score a false 100%. `test_databricks_notebook_helpers.py` reads `docs/`, so it was left out of the `magics.py` run.

The new tests point `LOCALAPPDATA` and `HOME` at the test's own folder, and use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `magics.py` | test_notebook_magics, test_notebook_magics_windows, test_notebook_formats_edges, test_opportunities_analyzers_mutation (+ test_notebook_magics_mutation) | 150 of 221 | 130 / 20 | **86.7%** | 147 / 3 | **98.0%** | 3 equivalent |
| `costs.py` | test_capacity*, test_estate*, test_measurement, test_risk_service_edges, test_usage_* (12 files, + test_costs_mutation) | 150 of 189 | 107 / 43 | **71.3%** | 150 / 0 | **100%** | none |
| `git/scopes.py` | test_git_scopes, test_git_cli_and_scopes_edges, test_git_service_edges, test_git_service_more_edges, test_git_service_mutation, test_git_service_platform_mutation, test_notebook_places (+ test_git_scopes_mutation) | 77 of 77 | 69 / 8 | **89.6%** | 76 / 1 | **98.7%** | 1 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **magics.py**:
  - A triple quote with text after it on its line still opening a string, so a magic inside it is left alone.
  - A string's own end: a bracket inside a string not counting, `\"` not ending a string, and the scan not running past a string's end.
  - Extra spaces between a magic's words making no empty words.
  - `returns_rows` being `False` (not just falsy) for empty SQL, and the last statement deciding, not the first.
  - A backticked name holding a verb before a `WITH`'s own verb not counting as the verb.
  - A backslash escaping inside quotes but not inside backticks, a backslash at the very end of an open quote kept, and a semicolon after a closed string splitting.
  - What follows `*/` kept in full.
  - → `test_notebook_magics_mutation.py`.
- **costs.py**:
  - Every F, P and trial SKU's size, and SKUs that have none.
  - Settings taking only known keys, and only when they have a value.
  - A time with an offset keeping it and one without being UTC.
  - Runs under a second counted, and negative or boolean times not.
  - The rough scenario being time here times the CUs of a Python notebook.
  - A metadata run keeping its name and environment when its time isn't known.
  - A Fabric run's workload needing both its workspace and item, its plain kind and name, and why its time isn't known.
  - The CUs and the wording for each engine, Table maintenance counted as Spark.
  - An unknown cost keeping its reason or saying "CU use isn't known", and the price note and method added only when there's CU to price.
  - "1 of 1 run ... has" and "2 of 2 runs ... have".
  - The report's rounding (seconds to 1 place, CU hours to 3, money to 2), items ranked by their cost plus what they avoided, then longest first, and each item's kind, name and environment.
  - A capacity's share over its whole period, and why it isn't known.
  - → `test_costs_mutation.py`.
- **git/scopes.py**:
  - `check_kind` returning the kind.
  - Changing a stated kind naming both kinds.
  - An unreadable `locations.toml` existing and saying why, and a readable one existing with no problem.
  - `runs_on = "this_pc"` winning over a workspace, and a workspace with no lakehouse attaching none.
  - A location field of 256 characters kept and one of 257 refused.
  - → `test_git_scopes_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `magics.py` | 189 | the bracket depth's sign flipped | Equivalent: only `depth == 0` is compared, and flipping every step's sign keeps that the same. |
| `magics.py` | 213 | `i < 0` → `i < 1` after finding the end of a `--` comment | Equivalent: the newline found is at least two characters past the `--`, so it is never at 0. |
| `magics.py` | 218 | `end < 0` → `end < 1` after finding `*/` | Equivalent: `*/` is searched for from two past the `/*`, so it is never found at 0. |
| `git/scopes.py` | 156 | `parents=True` → `False` when writing a new repository's files | Equivalent: every starting file is at most one folder deep, and the repository's own folder already exists. |

### Not in this batch

Batch 34's modules, which ran at the same time.

# Mutation testing, batch 35

Three modules, none touched by an open pull request, each with tests that run in under 20 seconds:
- The CPU cap for side by side's "Match Fabric": which processors the affinity is set to off Windows and on Windows, and how kernel32 is loaded (`cpu_cap.py`).
- Databricks SQL's IP functions on this PC: which BINARY lengths are read, how an IPv4 block is compared with IPv6, the error each kind of input names, and how the functions are registered with DuckDB (`databricks_ip.py`).
- Matching imported Capacity Metrics and cost rows to this app's Fabric runs: the coverage window and its tolerance, which runs and rows count, how a match reads, and the bill per capacity (`usage/reconcile.py`).

Batch 33's modules (costs, git scopes and notebook magics) were left to batch 33, which is still running.

One follow-up from batch 34's review is also here. `test_spark_sql_params_mutation.py` now checks that `SELECT : lim` has no marker, since Spark needs `:name` written together. That kills the `nxt.start == tok.end + 1` → `>=` mutant at `spark_sql_params.py` line 77, which batch 34's sample of 150 hadn't picked.

The run was at `main` 585b41c87 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 34 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways (474 passed), so no run below can score a false 100%. Three tests in `test_databricks_coverage.py` read `docs/`, so they were left out of the `databricks_ip.py` run: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.

The new tests write no files and use no network or timing. The CPU cap tests replace every affinity call and kernel32 with stand-ins, so they never change the test process's own cores.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cpu_cap.py` | test_cpu_cap, test_side_by_side_cores (+ test_cpu_cap_mutation) | 52 of 52 | 48 / 4 | **92.3%** | 52 / 0 | **100%** | none |
| `databricks_ip.py` | test_databricks_ip_bytes_edges, test_databricks_builtins_sql_mutation, test_databricks_coverage (+ test_databricks_ip_mutation) | 104 of 104 | 97 / 7 | **93.3%** | 104 / 0 | **100%** | none |
| `usage/reconcile.py` | test_usage_import (+ test_usage_reconcile_mutation) | 138 of 138 | 104 / 34 | **75.4%** | 135 / 3 | **97.8%** | 3 equivalent |

One `cpu_cap.py` mutant (`bit += 1` → `bit += 0`) loops forever and is counted as killed by its timeout, as in earlier batches.

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **cpu_cap.py**:
  - Off Windows, the processors this process may use now read for pid 0, and the first of them picked in order.
  - On Windows, processor 0 picked first when it is allowed.
  - kernel32 loaded with `use_last_error=True`, both when the affinity is read and when it is set, so the error a refusal raises is the real one.
  - → `test_cpu_cap_mutation.py`.
- **databricks_ip.py**:
  - A BINARY value of a length other than 4, 5, 16 or 17 refused, even when its bytes spell an address as text.
  - A 17-byte BINARY value read as an IPv6 block.
  - An IPv4 block compared with IPv6 as its mapped block, exactly 96 bits longer.
  - The error for an input that may be an address or a block naming both, and the one for a block naming only a block.
  - Both functions registered with `null_handling="special"` and `side_effects=False`.
  - → `test_databricks_ip_mutation.py`.
- **usage/reconcile.py**:
  - A run up to 300 seconds before an import's first row or after its last row's end counted as covered, and one second more not.
  - An import's span ending at its last row's end, not its start.
  - A capacity bill, which has no workspace, covering no run.
  - A run counted only with a start inside the period and a job id, matched only by rows that start before it finished, and measured up to now while it is still going.
  - `by_run` listing the newest first and at most 200.
  - A timed row outside the period not counted, and an item total wholly outside the period not counted as partly inside it.
  - One matched row described in the singular, and the run's workspace id carried, or none.
  - Every count starting at zero and counting one per run.
  - A run without an estimate saying why by its kind.
  - `by_run` naming the workspace the run recorded or its id maps to.
  - The measured-to-estimate ratio kept to three places.
  - An item name the run recorded winning, and table maintenance without its table at the end of its name having none.
  - Capacities listed by name even with one row, the busiest eight meters named, and of two exports reaching as far, the one imported last winning.
  - → `test_usage_reconcile_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `usage/reconcile.py` | 49 | `name[: -len(table) - 1]` → `name[: -len(table) - 0]` | Equivalent: the space that's left is stripped. |
| `usage/reconcile.py` | 85 | `end or start` → `end and start` | Equivalent: `_end` is set on each row but never read. |
| `usage/reconcile.py` | 87 | `imported_at or ""` → `imported_at and ""` | Equivalent: `_imported_at` is read only to rank capacity rows, and those always have a start, so they take the branch above. |

### Not in this batch

Batch 33's modules.

# Mutation testing, batch 36: boundary mutants

A Quality review of batch 35 found a gap in the tool itself. `sidecar/tools/mutate.py` flipped a comparison to its opposite (`<` to `>=`, `<=` to `>`), but it never moved a comparison's boundary (`<` to `<=`, `>` to `>=`). So a value sitting exactly on an edge, which is where off-by-one bugs live, was never tested. Chained comparisons such as `since <= start < until` were already mutated one operator at a time; only the boundary move was missing.

**What changed in the method.** `mutate.py` now also makes boundary mutants: each `<`, `<=`, `>` and `>=` in a comparison is moved to its neighbour. `--kinds` chooses which mutants to make:
- `all` (the default from this batch on): the earlier mutants, then the boundary ones.
- `point`: only the earlier mutants. Their indexes are the same as before, so `--seed` and `--only` still match the runs in batches 1 to 35.
- `boundary`: only the boundary mutants.

**Earlier scores leave boundary mutants out.** The scores in batches 1 to 35 count only the mutants the tool made then. They are left as they were. Every run from this batch on includes boundary mutants.

The run was at `main` 805786e9 (2026-11-15) with `--kinds boundary` (every boundary point, no sampling), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Eight recent modules with fast tests were re-run, each with the test set its own batch lists. For cpu_cap, databricks_ip and usage/reconcile, that set includes batch 35's tests, which were still in review (#2688), so they were copied in for the runs only.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. The tests that read `docs/` are left out as in the batches listed.

## Results (boundary mutants only)

| Module | Test files run (its batch) | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cpu_cap.py` | batch 35's set | 2 | 2 / 0 | **100%** | no change needed | **100%** | none |
| `databricks_ip.py` | batch 35's set | 1 | 0 / 1 | **0%** | 1 / 0 | **100%** | none |
| `usage/reconcile.py` | batch 35's set | 13 | 3 / 10 | **23.1%** | 13 / 0 | **100%** | none |
| `spark_sql_params.py` | batch 34's set | 9 | 8 / 1 | **88.9%** | 8 / 1 | **88.9%** | 1 equivalent |
| `databricks_variant.py` | batch 28's set | 19 | 13 / 6 | **68.4%** | 15 / 4 | **78.9%** | 4 equivalent |
| `nbsymbols.py` | batch 27's set | 22 | 6 / 16 | **27.3%** | 13 / 9 | **59.1%** | 9 equivalent |
| `orchestration/expressions.py` | batch 31's set | 16 | 13 / 3 | **81.3%** | 16 / 0 | **100%** | none |
| `localdata/tsql_format.py` | batch 31's set | 12 | 9 / 3 | **75.0%** | 12 / 0 | **100%** | none |
| **All eight** | | 94 | 54 / 40 | **57.4%** | 80 / 14 | **85.1%** | 14 equivalent |

Every boundary mutant that isn't equivalent is now killed. The new tests are in `sidecar/tests/test_boundary_mutation.py`, added to each module's set for the "after" runs.

One real bug was found, filed as #2691, with a strict xfail that flips when it's fixed: on the local engine, T-SQL `FORMAT(value, 'N<p>')` fails with DuckDB's own conversion error when the value has more integer digits than `38 - p`, for example `FORMAT(1.5, 'N38')`.

### What the survivors were, and the tests that now kill them

- **usage/reconcile.py**:
  - A timed row starting exactly at the period's start counted, and one starting exactly at its end not.
  - An item total from exactly the start to exactly the end counted, and one a second past either edge counted as partly outside.
  - An item total that only touches the period (ends at its start or starts at its end) not counted as partly outside.
  - A run starting exactly at the period's end not in the period.
  - A row starting exactly when a run starts, or exactly when it finishes, matched to it.
  - Of two bills for the same slot on a full tie, the first one seen kept.
- **databricks_ip.py**: a network BLOB with the longest prefix (/32, /128) accepted, and one longer refused.
- **databricks_variant.py**: a VARIANT DOUBLE of exactly 0.001 printed plain, and an index one past an array's end not found.
- **nbsymbols.py**: definitions on a cell's last line with no newline after it (`def`, `import`, `except ... as`, a `case` name), a name on the last line after a wide character, the cursor right after a name, and a cell index one past the last.
- **orchestration/expressions.py**: an interpolation at the start of a value that ends with `@`, and expressions cut short (`output[`, `output['x'`, `concat(`) reported as not closed, keeping the references they could read.
- **localdata/tsql_format.py**: `N38` and seven `f`s accepted (the most each takes), and `yy` giving the two-digit year.
- → `test_boundary_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_sql_params.py` | 43 | `value > 0` → `value >= 0` | Equivalent: the value there is always an infinity (as in batch 34). |
| `databricks_variant.py` | 59 | `copysign(1, value) < 0` → `<= 0` | Equivalent: `copysign(1, x)` is 1 or -1, never 0. |
| `databricks_variant.py` | 64 | `value < 0` → `value <= 0` | Equivalent: zero returns earlier. |
| `databricks_variant.py` | 219 | `0 < at` → `0 <= at` | Equivalent: `at` starts at 1 or -1 and only grows from 1, so it is never 0. |
| `databricks_variant.py` | 219 | `at < len(text)` → `at <= len(text)` | Equivalent: at the end, the step pattern matches nothing and the loop stops the same way. |
| `nbsymbols.py` | 118, 125, 134, 148, 220, 225, 230, 256, 268 | `0 < line` → `0 <= line` (9 mutants) | Equivalent: Python's line numbers start at 1, so a line is never 0. |

### Not in this batch

The other modules from batches 1 to 35. They can be re-run with `--kinds boundary` in later batches.

# Mutation testing, batch 37: boundary mutants, older modules

Batch 36 taught `sidecar/tools/mutate.py` to move a comparison's boundary (`<` to `<=`, `>` to `>=`) and re-ran eight recent modules. This batch runs the boundary mutants on eight older modules from batches 1 to 12, chosen for their limits, deadlines, size caps and date edges, and with test sets that run in seconds. It also adds `--kinds` and `--only` to the tool's usage message, which still left them out.

The run was at `main` a9e1bd2e (2026-11-15) with `--kinds boundary` (every boundary point, no sampling), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Each module was run with the test set its own batch lists, apart from `meta/mesh.py`. Batch 12's set for it takes 82 seconds a run, so this run leaves out its five slowest files (test_platform, test_platform_service_paths, test_schema_evolution, test_git_scopes, test_meta_atomic_save) and keeps the other eight.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results (boundary mutants only)

| Module | Test files run (its batch) | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `retrying.py` | batch 1's set | 5 | 1 / 4 | **20.0%** | 5 / 0 | **100%** | none |
| `leases.py` | batch 4's set | 13 | 9 / 4 | **69.2%** | 13 / 0 | **100%** | none |
| `onelake.py` | batch 4's set | 11 | 11 / 0 | **100%** | no change needed | **100%** | none |
| `maintenance.py` | batch 2's set | 8 | 6 / 2 | **75.0%** | 8 / 0 | **100%** | none |
| `localdata/discovery.py` | batch 4's set | 9 | 6 / 3 | **66.7%** | 8 / 1 | **88.9%** | 1 equivalent |
| `widgets.py` | batch 11's set | 20 | 12 / 8 | **60.0%** | 19 / 1 | **95.0%** | 1 equivalent |
| `tmdl.py` | batch 12's set | 19 | 14 / 5 | **73.7%** | 18 / 1 | **94.7%** | 1 equivalent |
| `meta/mesh.py` | batch 12's set, less its five slowest files | 12 | 9 / 3 | **75.0%** | 12 / 0 | **100%** | none |
| **All eight** | | 97 | 68 / 29 | **70.1%** | 94 / 3 | **96.9%** | 3 equivalent |

Every boundary mutant that isn't equivalent is now killed. The new tests are in `sidecar/tests/test_boundary_mutation_2.py`, added to each module's set for the "after" runs. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **retrying.py**:
  - A poll still unfinished at exactly its deadline looks once more.
  - A wait that would end exactly at the deadline isn't started, and one ending just before it is.
  - A remote wait is slept in naps with no empty nap at the end.
  - A dropped connection is tried again when the time left under the cap exactly fits the wait.
- **leases.py**:
  - A lease at exactly its expiry isn't taken over yet.
  - A lease left unrenewed for exactly its length counts as lost.
  - The file lock gives up at exactly its deadline, on Windows and elsewhere. Both are run with a stand-in lock call and clock, so each runs on every OS.
- **maintenance.py**: a file exactly at the age limit isn't older yet, and a commit made exactly when a stopped run started counts as after it.
- **localdata/discovery.py**: a file named only by its suffix (`.csv`) isn't a data file, and exactly as many files as the limit isn't reported as over it.
- **widgets.py**:
  - A slider whose min equals its max is allowed.
  - The registry keeps exactly `MAX_WIDGETS` widgets.
  - Printing exactly the stream cap isn't truncated.
  - A tab title one past the end is empty.
  - An index one past the options is refused by name, in both a dropdown and a multi-select.
  - The app selecting one past the options is ignored.
  - `interact` on 0 gives a slider from 0 to 1.
- **tmdl.py**:
  - TMDL of exactly the size limit is read.
  - A model writing exactly the size limit is written.
  - A model with exactly the table limit is written.
  - A fenced expression that's never closed runs to the end of the file.
- **meta/mesh.py**:
  - Two days in a row are listed, not written as a range.
  - A link to a product that stops today blocks the promote.
  - A product deprecated today has given its readers notice.
- → `test_boundary_mutation_2.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `localdata/discovery.py` | 173 | `formats <= {...}` → `formats < {...}` | Equivalent: `formats` holds exactly one format there, so being a subset of the three and being a proper subset are the same. |
| `widgets.py` | 797 | the `min` choice's `v > 0` → `v >= 0` | Equivalent: it differs only at 0, where `-v` and `3 * v` are both 0. |
| `tmdl.py` | 110 | `end < 0` → `end <= 0` | Equivalent: the search starts after the opening quote, so `find` returns -1 or at least 1, never 0. |

### Not in this batch

The other modules from batches 1 to 35, which later batches can re-run with `--kinds boundary`.

# Mutation testing, batch 38

Three modules not covered before, none touched by an open pull request, each with tests that run in under 30 seconds. This is the first batch of new modules run with the tool's default `--kinds all`, so each score counts the boundary mutants that batch 36 added (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Fabric CU avoided by a run on this PC, worked out from Fabric runs of the same workload: which baselines are recent enough, the five newest, scaling by rows read, and how the estimate is worded (`calibration.py`).
- Reading a usage file a person exported: the size and row limits, the separator, the decimal mark, day-first or month-first dates, and time zone offsets (`usage/reader.py`).
- Freshness promises for tables: checking a `[freshness]` table, when a table is late against `within_hours` or `by`, when it is next due, the order of the report, and rewriting the entity file (`meta/freshness.py`).

`databricks_bytes.py` (Databricks SQL's byte-level string functions) was run too. It scored 50 of 50 with its current tests and has no boundary points, so it needs no new tests.

The run was at `main` 811af46d0 (2026-11-15; the branch was then moved to 1dab3b763, which adds batch 37 and doesn't change these modules), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. Three tests in `test_databricks_coverage.py` read `docs/`, so they were left out of the `databricks_bytes.py` run, as in batch 35.

The new tests are pure: they write no files and use no network or timing. The usage reader tests use the module's real limits (a 64 MB file and a 250,000-row file are built in memory), not monkeypatched ones, so a changed limit fails them.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `calibration.py` | test_calibration (+ test_calibration_mutation) | 65 of 65 (60 + 5) | 45 / 20 | **69.2%** | 44/60 → 59/60 | 1/5 → 5/5 | 64 / 1 | **98.5%** | 1 equivalent |
| `usage/reader.py` | test_usage_dates, test_usage_finite, test_usage_import, test_usage_reader_store_edges (+ test_usage_reader_mutation) | 150 of 194 (140 + 10) | 125 / 25 | **83.3%** | 120/140 → 139/140 | 5/10 → 10/10 | 149 / 1 | **99.3%** | 1 equivalent |
| `meta/freshness.py` | test_dataops (+ test_meta_freshness_mutation) | 108 of 108 (103 + 5) | 76 / 32 | **70.4%** | 76/103 → 102/103 | 0/5 → 5/5 | 107 / 1 | **99.1%** | 1 equivalent |
| `databricks_bytes.py` | test_databricks_ip_bytes_edges, test_fips, test_databricks_coverage | 50 of 50 (50 + 0) | 50 / 0 | **100%** | 50/50 | none | no change needed | **100%** | none |

Every boundary mutant in the three modules is now killed: 6 of 20 were killed before. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **calibration.py**:
  - A Fabric run at this very moment, and one exactly at the age limit, used; one half a second past the limit not.
  - A measured baseline without a value left out.
  - A version with no baselines at all saying there is nothing to compare with, not that the versions differ.
  - Only the five newest runs used, with their date range named.
  - One run named in the singular.
  - Rows scaling only when both sides read more than none, and one row on each side still scaling.
  - The baselines' sources named, or "Fabric runs of this workload" when none is recorded.
  - Three measured runs firm, while three estimated or two measured runs rough.
  - → `test_calibration_mutation.py`.
- **usage/reader.py**:
  - A file of exactly 64 MB not refused for its size, one byte more refused with the limit named in MB, and the limit itself pinned.
  - Exactly 250,000 rows read and one more refused, with the message naming the limit.
  - An Excel workbook named as one.
  - The separator taken from the header line only.
  - Exponent notation such as `1e5` not read as a number.
  - `UTC` giving the UTC zone.
  - Offsets up to 14 hours and 59 minutes read (including `+02:30`), and `+15:00`, `+01:60` and `-15` refused.
  - `01/12/2026` still reading both ways, since 12 can be a month.
  - A short row giving empty cells for its missing columns.
  - → `test_usage_reader_mutation.py`.
- **meta/freshness.py**:
  - A promise described in words, for hours, a time of day or both.
  - A `[freshness]` that isn't a table saying how to write one.
  - Either half of a promise enough, any hours above 0 allowed (0.5 and 1), and 0 refused.
  - A run ending its seconds after it started, keeping its own offset; a start without one read as UTC; no seconds recorded meaning it ended when it started.
  - Age in hours to two places.
  - The due time given; exactly at the promised hours still fresh and a second later late.
  - By a time of day: late from that very minute, fresh a minute before, and a run at midnight counting as today, both for lateness and for the next due time.
  - The latest run's failure shown only when it didn't succeed.
  - The report listing late tables first, then never loaded, then fresh.
  - Removing a promise leaving no blank lines at the end.
  - → `test_meta_freshness_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `calibration.py` | 26 | `return False` → `return None` | Equivalent: `_usable` is only used as a filter, where None is as false as False. |
| `usage/reader.py` | 165 | `int(m.group(3) or 0) > 59` → `or 1` | Equivalent: with no minutes written the value is 0 or 1, and neither is over 59. |
| `meta/freshness.py` | 129 | `"fresh": 2` → `3` in the report order | Equivalent: fresh still sorts after late (0) and never loaded (1). |

# Mutation testing, batch 39: boundary mutants, older modules (2)

A second sweep of older modules with `--kinds boundary`, the same way as batch 37. It takes seven modules that batches 36 and 37 didn't re-run, chosen for their comparisons and limits: SQL scanning with token indexes, size and age caps, deadlines and grace periods.

The run was at `main` 1dab3b76 (2026-11-15) with `--kinds boundary` (every boundary point, no sampling), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Each module ran with its own mutation test file and the test files that cover it most closely, all of which run in seconds:

- `risk.py`: test_risk, test_risk_service_edges, test_compute_check_ast, test_model_bpa, test_risk_mutation.
- `appstudio/spec.py`: test_appstudio, test_appstudio_spec_edges, test_appstudio_generate(_edges), test_appstudio_edit(_edges), test_appstudio_pbir(_edges), test_appstudio_spec_mutation.
- `sqlcompat/practices.py`: test_sql_practices, test_learn_fixes, test_learn_guides, test_sqlcompat_practices_mutation.
- `frames.py`: test_frames, test_frames_more_edges, test_notebook_display_edges, test_outputs_hardening, test_query_profile, test_spark_summary_schema, test_wrangler, test_write_types, test_frames_mutation.
- `spark_number_format.py`: test_spark_number_format_contract, test_spark_builtins_sql(_edges, _mutation), test_snowflake_sql, test_spark_number_format_mutation, limited to `-k "number or to_char or to_varchar"`.
- `sqlcompat/rules.py`: test_learn_fixes(_edges), test_learn_guides, test_localdata_engine_mutation, test_snowflake_sql_variadic, test_sql_compat, test_sql_practices, test_tsql_contract, test_sqlcompat_rules_mutation.
- `mailbox.py`: test_mailbox(_edges, _more_edges), the test_agent files, test_paging, test_lease_clock_skew, test_shared_schedules, test_schedule_overlap, test_sqlite_connections_closed, test_not_set_up, test_cli_local_commands, test_contract_onelake, test_mailbox_mutation.

The tool runs pytest with `-x` in a copy of `sidecar/` only. A test that reads `docs/` or `examples/` fails there, and then every mutant reads as killed. The first runs of `practices.py`, `rules.py` and `mailbox.py` hit this, so they were thrown away and run again with those tests left out: `test_learn_guides.py::test_the_shipped_guides_are_the_docs` and `test_practice_docs.py` for the two SQL modules, and seven tests in `test_mailbox.py` plus one in `test_agent_queue_bound.py` that run the shipped example notebook. Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. The `mailbox.py` "after" run used only `test_mailbox_mutation.py` and `test_boundary_mutation_3.py`. A wider set can only kill more mutants, so 16 / 16 holds.

## Results (boundary mutants only)

| Module | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- |
| `risk.py` | 10 | 6 / 4 | **60.0%** | 10 / 0 | **100%** | none |
| `appstudio/spec.py` | 11 | 11 / 0 | **100%** | no change needed | **100%** | none |
| `sqlcompat/practices.py` | 40 | 15 / 25 | **37.5%** | 30 / 10 | **75.0%** | 10 equivalent |
| `frames.py` | 17 | 9 / 8 | **52.9%** | 17 / 0 | **100%** | none |
| `spark_number_format.py` | 12 | 8 / 4 | **66.7%** | no change possible | **66.7%** | 4 equivalent |
| `sqlcompat/rules.py` | 71 | 25 / 46 | **35.2%** | 62 / 9 | **87.3%** | 6 equivalent, 3 with no difference found |
| `mailbox.py` | 16 | 2 / 14 | **12.5%** | 16 / 0 | **100%** | none |
| **All seven** | 177 | 76 / 101 | **42.9%** | 154 / 23 | **87.0%** | 20 equivalent, 3 with no difference found |

The new tests are in `sidecar/tests/test_boundary_mutation_3.py`, added to each module's set for the "after" runs. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **risk.py**: a run that ended the moment it started took zero seconds; a run at exactly the CU-hour limit doesn't warn; a schedule due now, or at the window's edge, warns.
- **frames.py**:
  - The twentieth result still keeps the first, and the twenty-first evicts the least recently read.
  - A sort column or chart column one past the last is ignored or refused plainly.
  - Exactly sixty categories aren't truncated, and exactly seven series have no "other" slot.
  - A result of exactly a sheet's rows still goes to Excel.
  - Excel's first day (1900-01-01) is written as a date, and the minute before it as text.
- **sqlcompat/practices.py**: statements that end exactly at a keyword (`SELECT TOP`, `INTO`, `LIKE`, `ROW_NUMBER() OVER`, an unclosed subquery) give the same findings as before and don't fail; a `LIKE` after an `IN` list is still in the WHERE clause; a wrapped column after `GROUP BY` isn't a WHERE finding.
- **sqlcompat/rules.py**: 27 statements cut off at a boundary (a trailing `TABLE`, `#`, `LIMIT`, `PRIMARY`, `WITH a AS`, `CONVERT(string`, `ON SCHEMA::`, an unclosed type size, two `REFERENCES` in one table) are pinned to the exact rules and spans they report now.
- **mailbox.py**:
  - A request of exactly the byte limit, and a submitter name of exactly 200 characters, are read.
  - An empty request is rejected once exactly the writing grace has passed, and a run lookup Fabric couldn't answer is given up on at exactly its grace.
  - A deadline of this very second hasn't passed: at the door, when the request is queued, and while it waits in the queue.
  - A pipeline run from exactly a day ago, or exactly five minutes ahead, still vouches; one that vouched exactly a day ago still counts and is still remembered.
  - An owner at exactly its fresh time plus the grace (on OneLake) or its stale time plus the grace (on this PC) isn't lost yet.
  - A finished request's files go at exactly two weeks.
- → `test_boundary_mutation_3.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `sqlcompat/practices.py` | 137 | `hit["line"] - 1 < len(starts)` → `<=` | Equivalent: a hit's line is never past the last line, so it can't equal `len(starts)`. |
| `sqlcompat/practices.py` | 138 | `at < s.end` → `<=` | Equivalent: a statement's end is trimmed, so no line start equals it. |
| `sqlcompat/practices.py` | 188 | `i > 0` → `>=` | Equivalent: at 0, `toks[-1]` is the last token, which can't end where the first starts. |
| `sqlcompat/practices.py` | 217 | `j > i` → `>=` | Equivalent: token `i` is DISTINCT, so it can't be GROUP. |
| `sqlcompat/practices.py` | 274 | `where < i < end` → `<=` (both) | Equivalent: at `where` the token is WHERE and at `end` it is a clause end, and neither is a wrapping function. |
| `sqlcompat/practices.py` | 326 | `after >= close` → `>` | Equivalent: at `close` the token is `)` or the last one, never FROM, so the result is the same. |
| `sqlcompat/practices.py` | 385, 387 | `after < close` → `<=` | Equivalent: at `close` the token is `)` or the last one, never AS or a name that isn't a stop word. |
| `sqlcompat/practices.py` | 392 | `j + 3 >= close` → `>` | Equivalent: at `close` the token is neither a dot nor `(`. |
| `spark_number_format.py` | 92 | `first_digit < dollar`, `point < dollar` → `<=` | Equivalent: a digit or the point can't be at the dollar sign's index. |
| `spark_number_format.py` | 98, 161 | `i < point`, `i > point_at` → `<=`, `>=` | Equivalent: a group separator can't be at the point's index. |
| `sqlcompat/rules.py` | 462 | `k > 0` → `>=` | Equivalent: at 0, `toks[-1]` would have to be TOP, which ends no statement that reaches here. |
| `sqlcompat/rules.py` | 736, 741 | `j < end` → `<=` | Equivalent: at `end` the token is `)` or the last one, and both give the same result. |
| `sqlcompat/rules.py` | 791 | `i >= 2` → `>` | Equivalent: at 2, `toks[0]` is CREATE in a CTAS, not CONVERT. |
| `sqlcompat/rules.py` | 882 | `k > i` → `>=` | Equivalent: token `i` is a keyword at depth `d`, neither shallower nor a comma. |
| `sqlcompat/rules.py` | 980 | `i + 1 < len(toks)` → `<=` | Equivalent: at the last token the rest is empty, which gives the same result. |
| `sqlcompat/rules.py` | 296, 301 | `i + 3 < len(toks)`, `i + 2 < len(toks)` → `<=` | No difference found: they differ only for a name ending in `..` or `.` at the very end of a statement, which is already reported as SYN-007 either way. |
| `sqlcompat/rules.py` | 410 | `h.start < b` → `<=` | No difference found: no FN-001 hit was found that starts exactly where an explained span ends. |

### Not in this batch

The other modules from batches 1 to 35, which later batches can re-run with `--kinds boundary`.

# Mutation testing, batch 40

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, so each score counts the boundary mutants that batch 36 added (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- A run's scorecard: which side was faster (a tie counts for this PC), the estimated Fabric cost with its assumptions and rounding, this PC's measured zero job CU, the correctness counts, and the notes (`scorecard.py`).
- What an optimization opportunity is worth: imported CU per workload (a row counted once, rows without a value or time skipped), measured run time, the monthly figure scaled from the measured period (never below one day), Unknown when any part isn't measured, and a CU value that needs a price and a currency (`opportunities/impact.py`).
- `mapInPandas`, `mapInArrow` and `groupBy().applyInPandas` on this PC's Spark: the batch size setting, the schema check, empty results, null group keys, key order, a clean index in each group, and a function whose signature can't be read (`spark_map.py`).

The run was at `main` a13d0a0fc (2026-11-15; the branch was then moved to a1e0f60c9, which only adds Windows shell tests and doesn't change these modules), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

`test_prove.py` also reaches `scorecard.py`, through a whole proof run, but takes 46 seconds a run, so it was left out of the scorecard runs. That makes the scorecard's "before" lower than what the whole suite pins; the "after" needs only the two files listed.

The new tests write no files and use no network or timing. The Spark tests run code through a notebook kernel, as `test_spark_map.py` does.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scorecard.py` | test_scorecard_cost (+ test_scorecard_mutation) | 50 of 50 (49 + 1) | 20 / 30 | **40.0%** | 20/49 → 49/49 | 0/1 → 1/1 | 50 / 0 | **100%** | none |
| `opportunities/impact.py` | test_opportunities, test_opportunities_share, test_opportunities_analyzers_mutation, test_risk, test_workload_health (+ test_opportunities_impact_mutation) | 68 of 68 (68 + 0) | 48 / 20 | **70.6%** | 48/68 → 65/68 | none | 65 / 3 | **95.6%** | 3 equivalent |
| `spark_map.py` | test_spark_map (+ test_spark_map_mutation) | 74 of 74 (72 + 2) | 51 / 23 | **68.9%** | 50/72 → 65/72 | 1/2 → 2/2 | 67 / 7 | **90.5%** | 7 equivalent |

`opportunities/impact.py` has no `<` or `>` comparisons (its floors use `max`), so it has no boundary points. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **scorecard.py**:
  - No comparison without both times (None or 0 on either side); this PC faster, Fabric faster, and a tie reported for this PC.
  - The Python and Spark CU rates, the CU-seconds rounded to one place and the amount to four, an amount that is exactly CU hours times the price, the three assumptions word for word, and the price kept out of the CU measurement's assumptions.
  - Empty and None ids dropped from the measurement.
  - No job time (None or 0) giving "not available" with its reason and no figures.
  - This PC's side: a measured 0 at the finish time, the start time when there is no finish, and Unknown without either.
  - Correctness counts of tables, matches and levels; time and resources; the cost text for an estimate and for a run that never reached Fabric; the engine defaulting to Python.
  - The notes: Fabric's total only with both its times, the kernel process or the engine process (a CPU time of 0 still counts), and Fabric's own note.
  - → `test_scorecard_mutation.py`.
- **opportunities/impact.py**:
  - Imported rows summed per workspace and item (names trimmed and case-folded), their period spanned, a row with no end ending at its start, and the files named (or the import's id, or "an imported file").
  - A row counted once across imports, while a row skipped for having no value doesn't use up its key; a measured 0 still counted.
  - Run time and CU measured with their sources and methods, and each Unknown reason; a zero run total measured, not Unknown.
  - The monthly figure: each part scaled to 30 days, a period under a day counted as one, Unknown when any part is (with its reason, or "Not measured.").
  - The days of a measured period, and the fallback without both ends.
  - Avoidable impact for one workload, for two (no single workload id; Unknown CU when one has no usage) and for part of a workload; per-workload figures.
  - A CU value needing a known CU, a price above 0 and a currency, with the price's source in its assumptions.
  - → `test_opportunities_impact_mutation.py`.
- **spark_map.py**:
  - The default batch of 10,000 rows (10,001 rows come as 10,000 and 1), a setting of one row used, and a bad, zero or negative setting falling back to 10,000.
  - A schema that is neither DDL nor a StructType refused.
  - `mapInArrow` refusing a non-batch, and an empty result keeping the schema.
  - `applyInPandas` refusing a non-frame, keeping null keys as a group, calling groups in key order, giving each group a clean index with only the frame's columns (with and without the key), and passing only the frame to a function whose signature can't be read.
  - Grouping names from strings, other values and the `cols` attribute, and no columns refused.
  - → `test_spark_map_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `opportunities/impact.py` | 49 | `start is None or end is None` → `and` | Equivalent: `end` falls back to `start`, so it is None only when `start` is. |
| `opportunities/impact.py` | 89, 114 | `zip(..., strict=True)` → `strict=False` | Equivalent: both lists are built from the same workloads, so they are always the same length. |
| `spark_map.py` | 62, 73 | `barrier=False` → `True` | Equivalent: `barrier` is accepted for Spark's signature and not used on one PC. |
| `spark_map.py` | 70, 132 | `ignore_index=True` → `False` | Equivalent: the joined frame's index isn't used; `createDataFrame` reads only the columns. |
| `spark_map.py` | 90, 91 | `zip(..., strict=True)` → `strict=False` | Equivalent: a batch's columns have the same length, and the names match the columns. |
| `spark_map.py` | 124 | the early return for an empty frame dropped | Equivalent: an empty frame has no groups, so the loop adds nothing and the same empty result is returned. |

# Mutation testing, batch 41

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Running one query on two engines and comparing the answers: the default row limit, a side that fails or returns no table, which error a failed side quotes, and the refusals before anything runs, including a remote engine's result that needs the caller's copy confirm (`engines/compare.py`).
- Databricks DBUs billed, from `system.billing.usage`: the fixed query and its day choices, the row limit, which rows count as DBUs, the rounding, a quoted error's length, and the last read kept on this PC per workspace (`engines/databricks_billing.py`).
- Capacity settings history: the rows a read of capacities and of an administrator's overrides adds, and the read row's id and reason (`capacity_history.py`).

The run was at `main` fa9ed27b4 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

The new tests write only under pytest's temporary folder and use no network or timing. The compare tests use scripted sessions, and the billing tests a stand-in adapter, as the existing tests do.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/compare.py` | test_engine_compare (+ test_engine_compare_mutation) | 61 of 61 (60 + 1) | 51 / 10 | **83.6%** | 50/60 → 59/60 | 1/1 → 1/1 | 60 / 1 | **98.4%** | 1 equivalent |
| `engines/databricks_billing.py` | test_databricks_billing, test_engines_service_edges (+ test_databricks_billing_mutation) | 44 of 44 (43 + 1) | 28 / 16 | **63.6%** | 28/43 → 42/43 | 0/1 → 1/1 | 43 / 1 | **97.7%** | 1 equivalent |
| `capacity_history.py` | test_capacity_history (+ test_capacity_history_mutation) | 31 of 31 (31 + 0) | 26 / 5 | **83.9%** | 26/31 → 31/31 | none | 31 / 0 | **100%** | none |

`capacity_history.py` has no `<` or `>` comparisons, so it has no boundary points. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/compare.py**:
  - The default row limit of 1,000,000 given to both sides, and a limit of one row accepted.
  - A side's seconds rounded to four places.
  - A left side that is ok but returns no table: the right side doesn't run, and the problem says "returned no rows to compare", not "didn't give an answer".
  - A failed side quoting its last error, not its first, and "Error: " for an error with no name or value.
  - The refusal text: the engine's note added only when it has one.
  - A remote engine that can't return a result refused as unsupported, not as a missing copy confirm.
  - → `test_engine_compare_mutation.py`.
- **engines/databricks_billing.py**:
  - The query's `LIMIT 500`, the 500 result rows asked for, and no preview rows.
  - Each offered day count (1, 7, 30) taken and others refused.
  - A row with no job, pipeline, warehouse or cluster counted as "other" with no id.
  - Zero and fractional DBUs kept; NaN and negative DBUs each dropped.
  - DBUs rounded to three places and seconds to one.
  - A quoted error cut at 600 characters, and one of exactly 600 kept whole.
  - The per-workspace file named from the host with other characters as underscores, cut at 120 characters, and "workspace" for an empty host.
  - Saving making the folder and saving again over the last read.
  - → `test_databricks_billing_mutation.py`.
- **capacity_history.py**:
  - A read row's id: an administrator's read tagged `admin:`, a capacity read untagged, and a read with no capacity recorded under `-`.
  - A reason cut at 500 characters, and an empty reason kept as none.
  - An override row with no capacity id not taken as a capacity seen before.
  - → `test_capacity_history_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `engines/compare.py` | 99 | `assert ... and ...` → `or` | Equivalent: every part is already true at that point (the loop above returned otherwise), so the assert passes either way. |
| `engines/databricks_billing.py` | 87 | `round(total, 3)` → `round(total, 4)` | Equivalent: each row is already rounded to three places, so their sum differs from its three-place rounding only by float noise far below the fourth place. |

# Mutation testing, batch 42

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, as in batch 40. The table shows the point and boundary parts separately.

- The page of Spark read options this PC takes: each option of each format put to the reader's own check, honoured or refused, the note for a refusal other than the usual one, the known limits, the count table, the per-format tables and the names taken under another name (`spark_read_options.py`).
- The RPC parameter check: each parameter's JSON type checked against the outer type of the method's annotation, `Optional` and unions, `Any`, generics, required and unknown names, a method that takes any other name, and params that aren't an object (`rpc_params.py`).
- Batched loads: the batch size from free memory (the default when it can't be told, the floor), the plan that fills batches up to the target with empty values last, a batch's filter and text, and the steps folded by name with their details (`meta/batches.py`).

The run was at `main` 0df53df69 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

`test_spark_read_options.py::test_the_docs_page_is_up_to_date` compares the module's output with `docs/spark-read-options.md`, so it was left out of the runs. That makes `spark_read_options.py`'s "before" lower than what the whole suite pins. `test_rpc_fuzz.py` builds its wrong values from `rpc_params` itself, so a change to how a type is read also changed what it tried; the new tests use stand-in methods with fixed signatures and exact refusal messages.

The new tests write no files and use no network or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_read_options.py` | test_spark_read_options (3 tests) (+ test_spark_read_options_mutation) | 17 of 17 (17 + 0) | 3 / 14 | **17.6%** | 3/17 → 14/17 | none | 14 / 3 | **82.4%** | 3 equivalent |
| `rpc_params.py` | test_shortcut_request_only, test_rpc_fuzz (+ test_rpc_params_mutation) | 47 of 47 (47 + 0) | 39 / 8 | **83.0%** | 39/47 → 47/47 | none | 47 / 0 | **100%** | none |
| `meta/batches.py` | test_batches (+ test_meta_batches_mutation) | 50 of 50 (48 + 2) | 37 / 13 | **74.0%** | 36/48 → 46/48 | 1/2 → 2/2 | 48 / 2 | **96.0%** | 2 tuning constants |

`spark_read_options.py` and `rpc_params.py` have no `<` or `>` comparisons, so they have no boundary points. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_read_options.py**:
  - Rows in the file's order, each honoured or refused; no note for the usual refusal or for an option with no limit; a known limit found by the option's lower-case name.
  - Another refusal reason made into the note ("This PC's Spark ...").
  - The count table: honoured and total per format, a format with none.
  - Per-format sections in order, a `|` in a note written as `/`, and the line of names taken under another name, sorted, only where a format has them.
  - The page's first and last lines, and where the page is written.
  - → `test_spark_read_options_mutation.py`, with a stand-in options file.
- **rpc_params.py**:
  - Each plain type refusing every other JSON type, with true or false kept apart from numbers and a whole number taken as a number.
  - An optional type taking null and its own type only; `Any`, a union with `Any` and no annotation taking everything; only a generic's outer type checked.
  - A parameter without a default required, missing and unknown names listed in order, and a method that takes any other name.
  - The rules read from a signature, and a method whose type hints can't be read left to the call.
  - → `test_rpc_params_mutation.py`.
- **meta/batches.py**:
  - A batch's filter (inclusive of both ends, empty values only), its text, and "batch" only for one.
  - The batch size: the default without an estimate or free memory, the memory share over headroom per row, the floor and just above it.
  - The plan: values filling a batch up to exactly the target, one row over starting a new one, an oversized value on its own, value order, and empty values last as one batch (none when they count no rows).
  - Steps folded by name in first-run order, seconds rounded to milliseconds, a detail said once, three details all said, and a fourth or more counted.
  - → `test_meta_batches_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_read_options.py` | 98 | the `__name__ == "__main__"` guard negated or swapped | Equivalent for the tests: it only writes the docs page when run as a script. |
| `spark_read_options.py` | 46 | `has_schema=False` → `True` | Equivalent: each option is checked with no value, and the schema only matters for options that take one. |
| `meta/batches.py` | 24, 25 | `DEFAULT_BATCH_ROWS` 2,000,000 and `MIN_BATCH_ROWS` 50,000 → +1 | Tuning constants: the tests use them by name, and pinning their values would only restate them. |

# Mutation testing, batch 43

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The parity matrix: frozen cells and areas, the check that a familiar look needs a built-in skin, and the exact text of the tables and notes on the docs page (`parity.py`).
- Data Wrangler: the row, preview, step and split limits, which cast a column to a date gets, a sampled preview's first row, and each step's comment in the written cell (`wrangler.py`).
- Capacity and cost measurements: unit errors, time offsets, the 100% utilization limit, period checks, rounding, the stable id, freshness, and the wording of an incomplete total (`measurement.py`).

The run was at `main` c454d6d6b (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. Two tests in `test_parity.py` read `docs/` (`test_docs_page_matches_the_code` and `test_a_sql_rows_local_claim_follows_its_function_list`), so they were deselected from the parity runs; the "before" score for `parity.py` is lower than what the whole suite pins.

The new tests write nothing outside pytest's temporary folder and use no network or timing. The parity tests swap in their own platforms and areas, and the measurement freshness tests pass a fixed "now".

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `parity.py` | test_parity (two docs tests deselected), test_parity_edges (+ test_parity_mutation) | 61 of 61 (60 + 1) | 49 / 12 | **80.3%** | 49/60 → 57/60 | 0/1 → 1/1 | 58 / 3 | **95.1%** | 3 equivalent |
| `wrangler.py` | test_wrangler, test_wrangler_parity, test_outputs_hardening (+ test_wrangler_mutation) | 150 of 208 (147 + 3) | 140 / 10 | **93.3%** | 139/147 → 146/147 | 1/3 → 3/3 | 149 / 1 | **99.3%** | 1 equivalent |
| `measurement.py` | test_measurement, test_advisor, test_calibration, test_risk, test_scorecard_cost, test_usage_import, test_workload_capacity_use and the earlier calibration, costs, risk, scorecard, usage-reconcile, opportunities-impact and boundary mutation files (+ test_measurement_mutation) | 150 of 155 (145 + 5) | 132 / 18 | **88.0%** | 129/145 → 144/145 | 3/5 → 5/5 | 149 / 1 | **99.3%** | 1 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **parity.py**:
  - `Cell` and `Area` being frozen: changing either after it is made raises.
  - The skin check: a missing or unassessed look is accepted with no built-in skin, and a partial one is refused.
  - The docs table's exact text: one shared "Every area" note only when two or more areas of a platform have the same cell with a note, a platform with one area keeping its own note, a live run shown after the note, and a live run with no note still getting its line.
  - → `test_parity_mutation.py`.
- **wrangler.py**:
  - A result of exactly 1,000,000 rows previewed whole and one of 1,000,001 sampled; a sampled preview starting at the first row.
  - A preview of 100 rows.
  - 50 steps run and 51 refused.
  - A split into 2 or 20 parts taken, and 1 or 21 refused.
  - A timestamp column changed to a date by a cast, not by parsing text.
  - Each step's description written as its comment in the cell.
  - → `test_wrangler_mutation.py`.
- **measurement.py**:
  - An unknown unit named in the error whichever side it is on.
  - A time with its own offset keeping it.
  - A utilization of exactly 100% valid, and 100.5% refused.
  - A period whose start or end alone isn't a time refused.
  - Values, converted values and totals rounded to six places.
  - The id pinned as a fixed hash of 16 hex characters.
  - Freshness: no collection time is never stale, an observation exactly at its maximum age is fresh, and an age is never below zero.
  - An unknown measurement changing unit without a value.
  - The incomplete-total reason: "1 of 1 part" and "1 of 2 parts".
  - → `test_measurement_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `parity.py` | 227, 228, 229 | `areas[0]` → `areas[1]` (3 mutants) | Equivalent: these are reached only when a platform has more than one area, and a level is shared only when every area's cell equals the first one's, so the first and second areas give the same cell. |
| `wrangler.py` | 71 | `return "text"` → `return None` in `_kind` | Equivalent: `_literal` has no text branch, so a text column and a column of unknown kind both fall through to the same `repr(str(text))`. |
| `measurement.py` | 55 | `_STRENGTH` unknown 2 → 3 | Equivalent: only the order of the strengths matters, and 3 keeps unknown the weakest. |

# Mutation testing, batch 44

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, as in batch 42. The table shows the point and boundary parts separately.

- The Delta read estimate before a load: rows and bytes counted from the log's add actions after a watermark, files without a row count, the column share of the bytes, the memory width per row, sizes in MB or GB, and the guard that stops a read that won't fit with its headroom (`meta/preflight.py`).
- One assessment of Fabric workloads: the opportunity that keeps each workload when two touch it, overlaps, the totals and the CU value only when the CU total is complete, the ranking, coverage and counts (`opportunities/assess.py`).
- Notes under a Query profile: a scan that read many rows and kept few through a wrapped column, and a cross join or join that multiplied rows, each at its exact threshold, with a join's inputs read from the step depths (`learn/evidence.py`).

The run was at `main` 8d6c0e7fb (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

The new tests write no files and use no network or timing. The preflight tests give `estimate` a stand-in table whose log holds chosen add actions, so the files without a row count, a log with no row counts at all and an empty read can be set up exactly; the assessment counts test replaces the analyzers and the inventory with stand-ins through `monkeypatch`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/preflight.py` | test_preflight, test_batches, test_meta_batches_mutation (+ test_meta_preflight_mutation) | 81 of 81 (78 + 3) | 71 / 10 | **87.7%** | 70/78 → 78/78 | 1/3 → 3/3 | 81 / 0 | **100%** | none |
| `opportunities/assess.py` | test_opportunities, test_opportunities_share, test_opportunities_analyzers_mutation, test_opportunities_impact_mutation (+ test_opportunities_assess_mutation) | 50 of 50 (49 + 1) | 35 / 15 | **70.0%** | 35/49 → 49/49 | 0/1 → 1/1 | 50 / 0 | **100%** | none |
| `learn/evidence.py` | test_learn_evidence, test_learn_service (+ test_learn_evidence_mutation) | 80 of 80 (73 + 7) | 62 / 18 | **77.5%** | 62/73 → 71/73 | 0/7 → 7/7 | 78 / 2 | **97.5%** | 2 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/preflight.py**:
  - Sizes switching to GB at exactly 1 GB, and both units divided by powers of 1024.
  - An estimate exact unless told otherwise, and its text for one file and for a count that is "at least".
  - Files without a row count adding no rows and making the count inexact; a log with no row counts at all; nothing after the watermark reading no bytes, and a file at the watermark left out; an empty schema reading no columns.
  - Free memory as the platform reports it, and a read that fits exactly with its headroom allowed to run.
  - → `test_meta_preflight_mutation.py`.
- **opportunities/assess.py**:
  - Of two equally strong opportunities on one workload, the first keeps it.
  - Overlaps naming only the other opportunities that share a workload, never the opportunity itself or one that shares nothing.
  - The CU value given when the CU total is complete, and the reason when it isn't.
  - Coverage by type, analyzed, not read, types not analyzed and history not read, counted item by item; the counts of an assessment by category, confidence and action, and how many are counted.
  - → `test_opportunities_assess_mutation.py`.
- **learn/evidence.py**:
  - The thresholds at their exact edges: a scan of exactly 100,000 rows, a scan that kept exactly a quarter, a join of exactly 100,000 rows, a join that grew exactly ten times, a cross join that made only its larger input, and 1% shown as a percentage.
  - A join with three inputs gets no note.
  - A scan or join without a kept count, an input count or a share shows zero.
  - A join's inputs stopping at the next step at its level, and a step with no depth taken as the top.
  - → `test_learn_evidence_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `learn/evidence.py` | 70 | a scan's missing `rows_scanned` read as 1 instead of 0 | Equivalent: both are below the 100,000-row minimum, so the scan gets no note either way. |
| `learn/evidence.py` | 85 | a join's missing `rows` read as 1 instead of 0 | Equivalent: both are below the 100,000-row minimum, so the join gets no note either way. |

# Mutation testing, batch 45

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Windows time zones: the UTC offset read from a zone's display name, and the order the zones are listed in (`time_zones.py`).
- Team guidance: which of two policies at the same level is named, the order of team notes, and the note count, note length and unknown-id limits at their edges (`learn/team.py`).
- Copy job definitions: the reason a job can't run on this PC, naming each end's connection type (`orchestration/copyjob.py`).

The run was at `main` 2d26dcabb (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out.

The new tests are pure functions over values they build themselves: they write no files and use no network, registry or timing. The time zone tests pass their own reader, so they run the same on any OS.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `time_zones.py` | test_time_zones, test_schedule_locks_and_lists (+ test_time_zones_mutation) | 31 of 31 (31 + 0) | 28 / 3 | **90.3%** | 28/31 → 31/31 | none | 31 / 0 | **100%** | none |
| `learn/team.py` | test_learn_team (+ test_learn_team_mutation) | 47 of 47 (44 + 3) | 41 / 6 | **87.2%** | 41/44 → 44/44 | 0/3 → 3/3 | 47 / 0 | **100%** | none |
| `orchestration/copyjob.py` | test_workspace_copy_jobs, test_landing_copy_requests (+ test_copyjob_mutation) | 34 of 34 (34 + 0) | 33 / 1 | **97.1%** | 33/34 → 34/34 | none | 34 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **time_zones.py**:
  - An offset is hours times 60 plus minutes, for a half-hour, a three-quarter-hour and a negative zone.
  - Plain "(UTC)" and a name with no offset are offset zero.
  - Zones sort by offset, with UTC between the minus and plus zones and a one-minute difference keeping its order.
  - → `test_time_zones_mutation.py`.
- **learn/team.py**:
  - Two policies setting the same level keep the first one's name.
  - Exactly 200 notes are kept with no problem, and 201 are cut to 200 with one problem.
  - A note of exactly 2,000 characters is kept whole, and one more character cuts it.
  - An unknown id is quoted to 40 characters in its problem.
  - Notes given as text are a problem, never read as a definition's `{"value": ...}`.
  - Notes for one id follow policy name order, then policy id order.
  - → `test_learn_team_mutation.py`.
- **orchestration/copyjob.py**:
  - The reason names the end's connection type, then its type, then "not set".
  - → `test_copyjob_mutation.py`.

# Mutation testing, batch 46

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Opportunity records: what an opportunity must have to be made, its id, and what `problems` reports (`opportunities/model.py`).
- Landing Copy job requests: the checks on what a team asks to copy, and the Copy job definition built from it (`orchestration/copyjob_build.py`).
- Keeping a table family from DefaultReader: which role definitions are changed automatically and which are refused (`optimize/roles.py`).

The run was at `main` 043423b6d (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out.

The new tests are pure functions over values they build themselves: they write no files and use no network, registry or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `opportunities/model.py` | test_opportunities (+ test_opportunities_model_mutation) | 40 of 40 (40 + 0) | 29 / 11 | **72.5%** | 29/40 → 40/40 | none | 40 / 0 | **100%** | none |
| `orchestration/copyjob_build.py` | test_landing_copy_requests (+ test_copyjob_build_mutation) | 51 of 51 (50 + 1) | 44 / 7 | **86.3%** | 44/50 → 50/50 | 0/1 → 1/1 | 51 / 0 | **100%** | none |
| `optimize/roles.py` | test_optimize_roles, test_optimize_service_edges (+ test_optimize_roles_mutation) | 66 of 66 (66 + 0) | 65 / 1 | **98.5%** | 65/66 → 66/66 | none | 66 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **opportunities/model.py**:
  - Each refusal in `make` is checked on its own: an unknown category, action or confidence, an effort or a risk outside low, medium and high, and no evidence, artifacts, validation steps or rollback.
  - The id is `opp-` and 16 hex digits.
  - Evidence without a source is reported by `problems`.
  - → `test_opportunities_model_mutation.py`.
- **orchestration/copyjob_build.py**:
  - A request that isn't an object is refused with its own message, not a crash.
  - Exactly 50 tables are accepted, and 51 are refused.
  - A bad table name is quoted at the start of the message, and a blank one says "(blank)".
  - The connection name is trimmed and kept to 200 characters.
  - The job never retries, and its part is JSON indented by two spaces.
  - → `test_copyjob_build_mutation.py`.
- **optimize/roles.py**:
  - A DefaultReader with no Path list, or with two, is refused and left for an admin to change.
  - → `test_optimize_roles_mutation.py`.

# Mutation testing, batch 47

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Shared schedules: the request each due time of a schedule several agents share becomes, its slot name and deadline, and the schedule id limit (`shared_schedules.py`).
- Dismissed best-practice findings: what a person chose to hide in their own Best practices panels, the 1,000-row limit and the checks on what is kept (`learn/dismissals.py`).
- Usage records: measured, billed or estimated usage in one unit each, and totals that never mix units (`engines/usage.py`).

The run was at `main` 04c276758 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

`test_learn_guides.py` holds the dismissal tests, and also one test that compares the shipped guides with `docs/`, which fails in the tool's copy of `sidecar/`. That file was run with `-k "dismiss or broken_file or policy_names"`, which keeps every dismissal test and leaves out the guide tests, so the docs test can't make every mutant read as killed. No other test run reads `docs/` or `examples/`. The `engines/usage.py` runs use `-k usage`, which keeps the usage tests of `test_engines.py` and `test_kudp_guardrails.py` and every test in the two usage files.

`sql_function_help.py` was also run (test_sql_function_help, 32 mutants) and scored 100% with its existing tests, so it needs no new test and isn't in the table.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `shared_schedules.py` | test_shared_schedules, test_agent_silent_failures (+ test_shared_schedules_mutation) | 32 of 32 (31 + 1) | 28 / 4 | **87.5%** | 28/31 → 31/31 | 0/1 → 1/1 | 32 / 0 | **100%** | none |
| `learn/dismissals.py` | test_learn_guides (dismissal tests) (+ test_learn_dismissals_mutation) | 39 of 39 (38 + 1) | 35 / 4 | **89.7%** | 35/38 → 38/38 | 0/1 → 1/1 | 39 / 0 | **100%** | none |
| `engines/usage.py` | test_usage_finite, test_kudp_guardrails, test_engines (usage tests) (+ test_engine_usage_mutation) | 35 of 35 (34 + 1) | 29 / 6 | **82.9%** | 29/34 → 34/34 | 0/1 → 1/1 | 35 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **shared_schedules.py**:
  - The schedule id limit is the one `agent.toml` enforces: an id of `MAX_ID` characters is accepted, and one more is refused.
  - Only an every-N-minutes schedule has an interval. A daily schedule that also carries `every_minutes` has none.
  - An agent at the deadline itself still creates the slot, and one past it doesn't.
  - The request file is the request as two-space-indented JSON, byte for byte.
  - → `test_shared_schedules_mutation.py`.
- **learn/dismissals.py**:
  - 1,000 dismissals are kept, and the next one pushes out the oldest.
  - An id is the first 16 hex characters of the SHA-256 of the rule, scope, place and line.
  - Rows that aren't dismissals (text, numbers, lists, an unknown scope) are left out, and the dismissals among them are kept.
  - A place name of exactly 512 characters is kept.
  - → `test_learn_dismissals_mutation.py`.
- **engines/usage.py**:
  - An unknown unit is refused and the message names the known ones.
  - Billed usage with no source is refused.
  - Zero usage is a real amount, not refused as negative.
  - A record as a dict has every field.
  - Totals round to six places.
  - A record can't be changed after its checks.
  - → `test_engine_usage_mutation.py`.

# Mutation testing, batch 48

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. None of these modules has a `<` or `>` compare, so every mutant is a point mutant.
- VL_Sources, the source systems' settings as a Fabric variable library: its rows, definition files and reading any library's values (`meta/varlib.py`).
- The optimization lakehouses this PC is connected to, and which one is active (`optimize/registry.py`).
- An assessment as rows for the shared Optimize lakehouse (`opportunities/share.py`).

The run was at `main` f1c8892ef (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out. `test_estate.py` takes over a minute in full, so the `meta/varlib.py` runs used `-k "varlib or variable or library or keys_workspace or definition_files"`, which keeps its 11 variable library tests and the new ones.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/varlib.py` | test_estate, filtered (+ test_meta_varlib_mutation) | 46 of 46 (46 + 0) | 41 / 5 | **89.1%** | 41/46 → 45/46 | none | 45 / 1 | **97.8%** | 1 equivalent |
| `optimize/registry.py` | test_optimize_setup, test_corrupt_state_reads, test_optimize_service (+ test_optimize_registry_mutation) | 31 of 31 (31 + 0) | 26 / 5 | **83.9%** | 26/31 → 31/31 | none | 31 / 0 | **100%** | none |
| `opportunities/share.py` | test_opportunities_share (+ test_opportunities_share_mutation) | 49 of 49 (49 + 0) | 31 / 18 | **63.3%** | 31/49 → 49/49 | none | 49 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/varlib.py**:
  - The Keys workspace rows (`KEYS_WORKSPACE_ID`, `KEYS_WORKSPACE_NAME`) appear only when an environment names a Keys workspace.
  - The definition files are written with two-space indents, as Fabric's own export writes them.
  - → `test_meta_varlib_mutation.py`.
- **optimize/registry.py**:
  - A connection is refused if it lacks either the workspace or the lakehouse, and nothing is saved.
  - Connecting again keeps that lakehouse's own last check, and never another lakehouse's.
  - Forgetting the active lakehouse makes the newest one left active.
  - `connections.json` is written with one-space indents.
  - → `test_optimize_registry_mutation.py`.
- **opportunities/share.py**:
  - The existing tests run a whole assessment, where every opportunity has every field. The new ones build opportunities by hand.
  - A finding's detail names each item by name, or by id when it has none, with its role. It names the overlaps only for an opportunity that isn't counted, and lists the check and undo steps only when there are any.
  - An item needs both a workspace and an item id, and an opportunity with none of those is skipped.
  - A run is partial when the budget was reached or any coverage wasn't read.
  - The run id is `oe-` and 16 hex digits of the compact, key-sorted JSON, whatever the order of the workspaces.
  - The evidence and impact JSON is compact and key-sorted. One more, recorded with the security notes.
  - → `test_opportunities_share_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `meta/varlib.py` | 21 | `if TYPE_CHECKING:` negated | Equivalent: `Project` is only used in annotations, which `from __future__ import annotations` never evaluates, and `meta/project.py` doesn't import `varlib`, so importing it at run time changes nothing. |

# Mutation testing, batch 49

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Reliability objectives: the success objective a table declares or gets from its criticality, the checks on `[objectives]`, and how a table stands against its objective (`meta/objectives.py`).
- Credential facts: what an agent publishes about its own credential, never the credential itself (`credential_facts.py`).
- Engine registry: every engine KratosForge knows, its adapter, and whether Apache Spark could run on this PC (`engines/registry.py`).

The run was at `main` 621707162 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test run reads `docs/` or `examples/`, and no test file was filtered.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/objectives.py` | test_incidents (+ test_meta_objectives_mutation) | 62 of 62 (57 + 5) | 52 / 10 | **83.9%** | 51/57 → 57/57 | 1/5 → 5/5 | 62 / 0 | **100%** | none |
| `credential_facts.py` | test_run_as (+ test_credential_facts_mutation) | 28 of 28 (28 + 0) | 26 / 2 | **92.9%** | 26/28 → 28/28 | none | 28 / 0 | **100%** | none |
| `engines/registry.py` | test_engines, test_engine_switch_failures, test_engine_conformance (+ test_engine_registry_mutation) | 22 of 22 (22 + 0) | 20 / 2 | **90.9%** | 20/22 → 22/22 | none | 22 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/objectives.py**:
  - Five runs are the fewest that say anything: four counted runs are "not enough", five are judged, and `runs = 5` is accepted while 4 is refused.
  - `[objectives]` that isn't a table gets exactly one message.
  - The success rate is rounded to one place (4 of 6 is 66.7).
  - A success rate is a percent above 0, up to 100: 100 and 0.5 are accepted, and 0, 101, 100.5, -1, true and text are refused.
  - → `test_meta_objectives_mutation.py`.
- **credential_facts.py**:
  - A status record reads a password-protected certificate with the password its auth names.
  - A token error is kept to 200 characters.
  - → `test_credential_facts_mutation.py`.
- **engines/registry.py**:
  - An unknown engine is a `ValueError` that lists the known engines.
  - Apache Spark is ready only with both PySpark and Java, and the message names what is missing.
  - → `test_engine_registry_mutation.py`.

# Mutation testing, batch 50

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. None of these modules has a `<` or `>` compare, so every mutant is a point mutant.
- App Studio's starting templates: blank, sales overview and operations status (`appstudio/templates.py`).
- App Studio's local preview, made-up numbers for a specification (`appstudio/sample.py`).
- The local store of imported usage files (`usage/store.py`).

The run was at `main` aac8ff6ba (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out, and no `-k` filter was used.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/templates.py` | test_appstudio_service (+ test_appstudio_templates_mutation) | 69 of 69 (69 + 0) | 23 / 46 | **33.3%** | 23/69 → 69/69 | none | 69 / 0 | **100%** | none |
| `appstudio/sample.py` | test_appstudio_service, test_appstudio_connections (+ test_appstudio_sample_mutation) | 70 of 70 (70 + 0) | 52 / 18 | **74.3%** | 52/70 → 70/70 | none | 70 / 0 | **100%** | none |
| `usage/store.py` | test_usage_reader_store_edges, test_usage_import (+ test_usage_store_mutation) | 25 of 25 (25 + 0) | 23 / 2 | **92.0%** | 23/25 → 25/25 | none | 25 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **appstudio/templates.py**:
  - The existing test checks that every template is a valid specification for every target. Valid layouts can still overlap or leave holes, so 42 of the survivors were grid positions and sizes.
  - Every page of the sales and operations templates now has to tile the 12-column grid with no overlap and no gap, and the card and chart rows are pinned.
  - An unknown target is refused by name.
  - For a KratosForge app, interactions the target can't express are dropped, and a Power BI report keeps them. No shipped template has such an interaction yet, so the test adds a drillthrough to a test-only template.
  - → `test_appstudio_templates_mutation.py`.
- **appstudio/sample.py**:
  - A sample number is the first 4 bytes of the sha256 of its parts, in the range 1,000 to 99,999. A slicer on another field shrinks it by 0.15 to 0.39. Both are checked against the formula, not against stored numbers.
  - A field's column is the text inside its brackets, and an unknown column gets five numbered categories.
  - A card without a field shrinks once by the selection. A card with a field sums its categories and shrinks by a slicer on another field. A card without measures shows 0 and no measure name.
  - A chart without a field shows one value, like a card.
  - A category list longer than 12 is cut to 12 rows. No built-in list is longer than 12, so the test adds one.
  - → `test_appstudio_sample_mutation.py`.
- **usage/store.py**:
  - Imports are listed newest first, and one without an import time sorts last instead of breaking the list.
  - → `test_usage_store_mutation.py`.

# Mutation testing, batch 51

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Engine capabilities: the levels, evidence and rules every engine descriptor must meet, and whether an engine can run a cell (`engines/capabilities.py`).
- Capability registry: where each platform-neutral capability runs on every engine, and the table in the capabilities page (`capability_registry.py`).
- Model data dictionary: the semantic model's tables, columns and measures as an Excel CSV, and the preview of an edited one (`model_dictionary.py`).

The run was at `main` 7c92b98b5 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. `test_capability_registry.py` has one test that compares `docs/capabilities.md` with the code. That page isn't in the copy the tool runs in, so the two capability runs leave it out with `-k "not docs_page"`. The new registry tests check the same table against a fixed set of engines instead. No other test reads `docs/` or `examples/`. `test_model_practices.py` was not used, because it takes over a minute.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/capabilities.py` | test_engines, test_capability_registry, test_engine_conformance (+ test_engine_capabilities_mutation) | 69 of 69 (69 + 0) | 57 / 12 | **82.6%** | 57/69 → 69/69 | none | 69 / 0 | **100%** | none |
| `capability_registry.py` | test_capability_registry, test_engines (+ test_capability_registry_mutation) | 39 of 39 (39 + 0) | 25 / 14 | **64.1%** | 25/39 → 39/39 | none | 39 / 0 | **100%** | none |
| `model_dictionary.py` | test_model_dictionary, test_model_dictionary_service_edges (+ test_model_dictionary_mutation) | 81 of 81 (79 + 2) | 57 / 24 | **70.4%** | 57/79 → 78/79 | 0/2 → 2/2 | 80 / 1 | **98.8%** | 1 equivalent |

One real bug was found: #2728. A data dictionary under the size limit with one cell over 131072 characters raises `csv.Error` from Python's `csv` module, instead of the `ValueError` every other bad file gets. A strict xfail in `test_model_dictionary_mutation.py` pins it until it's fixed.

### What the survivors were, and the tests that now kill them

- **engines/capabilities.py**:
  - `Support.as_dict` lists the level, note and evidence.
  - Unknown evidence, an unknown language, and asking for an unknown capability are each refused.
  - A bad location, platform or billing is refused on its own, one at a time.
  - `Support` and `EngineDescriptor` are frozen.
  - A live-verified remote engine may list an unsupported language without being Preview.
  - `check_cell` names what is planned only when something is, and gives the right reason for native, simulated and translated.
  - → `test_engine_capabilities_mutation.py`.
- **capability_registry.py**:
  - With two fixed engines, the capabilities table is checked line by line: the "(Preview)" mark, and which notes go under "Known differences". A partial remote claim's note is listed. A native note isn't, and neither is a partial claim without a note.
  - A table with no notes has no "Known differences" part.
  - `listing()` returns every capability.
  - Requirements are listed only where the capability runs.
  - `Capability` is frozen.
  - → `test_capability_registry_mutation.py`.
- **model_dictionary.py**:
  - The size limit is exactly 5,000,000 characters: a file of that size is read, and one more character is refused.
  - Only the first line decides the delimiter, and a tie between commas and semicolons means comma.
  - Contract types map to Delta types, and a missing type is a string.
  - Each of the counts is checked.
  - A hidden column goes out as "Yes".
  - Import numbers rows as Excel numbers them and counts rows that don't change anything.
  - A table row is found by its kind or by the "(table)" marker.
  - Tables and measures are never hidden, column types are kept, and a measure over an undescribed column says only "Sum of" the column.
  - → `test_model_dictionary_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `model_dictionary.py` | 43 | `c["type"] or e.columns.get(...)` to `and` | Equivalent: `mesh.merged_catalog` fills each column's `type` from `e.columns.get(name, "")`, so the fallback reads the same value the catalog already holds. |

# Mutation testing, batch 52

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- Where local pipeline run records live, and how a run left going by a stopped app is marked (`orchestration/runs.py`).
- What every usage provider gives the importer, and how a file's headers are matched to a layout (`usage/providers.py`).
- Azure Cost Management cost details and FOCUS exports (`usage/cost_management.py`).

The run was at `main` 13d7b79db (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out, and no `-k` filter was used.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing. `best_practice_checkers.py` was looked at first and left out, because it is part of governance's Check now.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/runs.py` | test_orchestration_runs_edges, test_pipeline_runs (+ test_orchestration_runs_mutation) | 34 of 34 (32 + 2) | 24 / 10 | **70.6%** | 24/32 → 32/32 | 0/2 → 2/2 | 34 / 0 | **100%** | none |
| `usage/providers.py` | test_usage_dates, test_usage_finite, test_usage_import, test_usage_reader_mutation, test_usage_reader_store_edges, test_usage_reconcile_mutation (+ test_usage_providers_mutation) | 34 of 34 (33 + 1) | 30 / 4 | **88.2%** | 30/33 → 33/33 | 0/1 → 1/1 | 34 / 0 | **100%** | none |
| `usage/cost_management.py` | the same usage files (+ test_usage_cost_management_mutation) | 71 of 71 (68 + 3) | 56 / 15 | **78.9%** | 56/68 → 68/68 | 0/3 → 3/3 | 71 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/runs.py**:
  - Trim keeps exactly the newest 200 records, and the list gives 50 by default and at least one.
  - A run id may be 64 characters and use every allowed character; 65 is refused.
  - Ids with other characters are refused (added after review; not a mutant the tool makes).
  - Recovery runs once per store: a run started after it is never marked interrupted by a later read, and a second call reports 0.
  - Runs this process is running are left as they are.
  - An interrupted run gets an end time, and keeps one it already had.
  - → `test_orchestration_runs_mutation.py`.
- **usage/providers.py**:
  - A layout can't be changed after it's made, and the base provider refuses to read rows.
  - When two layouts fit equally, the first one is kept.
  - A column with no header is never listed as left out.
  - → `test_usage_providers_mutation.py`.
- **usage/cost_management.py**:
  - A row with no billing currency, or whose period ends when it starts, is skipped with its reason.
  - A negative cost or quantity is a refund; zero and amounts under 1 are not.
  - Meter category, unit, charge type and pricing are kept as they are, and empty ones are none.
  - The capacity name comes from the resource id when the row has no name.
  - The duplicate-detection keys (`slot` and `key`) are pinned to their values. Earlier imports stored them, so a changed key would let the same file import twice.
  - → `test_usage_cost_management_mutation.py`.

# Mutation testing, batch 53

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Notebook maintenance statements: how a notebook's `OPTIMIZE`, `VACUUM` and `RESTORE` are read on this PC, and Spark's answer for each (`spark_maint.py`).
- Tenant settings history: hashing a setting's value, the newest snapshot per setting, the change rows a read adds, and the read row (`tenant_history.py`).
- App Studio PBIR: compiling a Power BI report specification to the PBIR files (`appstudio/pbir.py`).

The run was at `main` 7893f86a8 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_maint.py` | test_spark_maint, test_spark_merge, test_spark_refusals, test_time_travel (+ test_spark_maint_mutation) | 52 of 52 (52 + 0) | 36 / 16 | **69.2%** | 36/52 → 51/52 | none | 51 / 1 | **98.1%** | 1 equivalent |
| `tenant_history.py` | test_tenant_history, test_capacity_history (+ test_tenant_history_mutation) | 34 of 34 (33 + 1) | 28 / 6 | **82.4%** | 28/33 → 33/33 | 0/1 → 1/1 | 34 / 0 | **100%** | none |
| `appstudio/pbir.py` | test_appstudio_pbir, test_appstudio_pbir_edges, test_appstudio_publish, test_appstudio_publish_edges (+ test_appstudio_pbir_mutation) | 63 of 63 (61 + 2) | 51 / 12 | **81.0%** | 51/61 → 60/61 | 0/2 → 2/2 | 62 / 1 | **98.4%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_maint.py**:
  - `RESTORE ... TIMESTAMP AS OF` gives the timestamp, in UTC when it names no offset, with either quote.
  - A timestamp with its own offset keeps it, and one RESTORE can't read is refused, alone and inside a statement.
  - `OPTIMIZE ... WHERE` takes only partition column = value joined with AND: a comparison, a column on the right, an OR and a bare column are each refused.
  - Spark's answers count a missing metric as 0 and carry the ones Delta reported; a dry-run `VACUUM` lists the files, a real one answers with the table.
  - → `test_spark_maint_mutation.py`.
- **tenant_history.py**:
  - The canonical text sorts keys and keeps non-ASCII text as written.
  - Security groups and properties are put in a fixed order, including an empty group entry.
  - Of two snapshots taken at the same time, the first is kept.
  - A read with no tenant is recorded with "-", and its reason is cut to 500 characters.
  - → `test_tenant_history_mutation.py`.
- **appstudio/pbir.py**:
  - A report may have up to 1000 pages and 1000 visuals per page, and exactly the limit compiles.
  - Parts are two-space JSON with text kept as written.
  - Visuals with data cross-filter the others.
  - Positions are grid columns rounded to two places.
  - A visual name with other characters is refused.
  - → `test_appstudio_pbir_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `spark_maint.py` | 67 | `.replace(" ", "T", 1)` to `.replace(" ", "T", 0)` | Equivalent on Python 3.12 and later (the floor): `datetime.fromisoformat` takes a space between date and time as well as `T`, so replacing the first space or none reads the same value. |
| `appstudio/pbir.py` | 113 | `max(..., default=0)` to `default=1` | Equivalent: it only matters for a page with no visuals, where the height is `max(720, 0 * 60)` or `max(720, 1 * 60)`, 720 either way. |

# Mutation testing, batch 54

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- IPython's input and output history in a notebook: `In`, `Out`, the underscores and `%history` (`history.py`).
- Which Fabric item types work with Fabric Git, deployment pipelines and item definitions (`artifacts.py`).
- Workspaces page previews: a table's first rows, a file's start, a notebook's cells (`preview.py`).

The run was at `main` 59968c9b3 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out, and no `-k` filter was used.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing. Two modules were run first and found already at 100% with their existing tests, so they got no new tests: `optimize/local_runs.py` (42 / 42: 41 point + 1 boundary, with test_optimize_service_mutation and test_optimize_local_runs) and `sqlitefile.py` (33 / 33, all point, with test_sqlite_stores, test_sqlite_busy and test_workload_store_faults). `learn/datasets.py` was looked at and left for later, because its tests take nearly two minutes.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `history.py` | test_history_edges, test_notebook_history, test_notebook_magics_windows (+ test_history_mutation) | 75 of 75 (68 + 7) | 67 / 8 | **89.3%** | 63/68 → 66/68 | 4/7 → 6/7 | 72 / 3 | **96.0%** | 3 equivalent |
| `artifacts.py` | test_artifacts, test_fabric_git_service_mutation, test_fabric_git (+ test_artifacts_mutation) | 60 of 60 (60 + 0) | 29 / 31 | **48.3%** | 29/60 → 60/60 | none | 60 / 0 | **100%** | none |
| `preview.py` | test_preview (+ test_preview_mutation) | 75 of 75 (73 + 2) | 59 / 16 | **78.7%** | 59/73 → 69/73 | 0/2 → 2/2 | 71 / 4 | **94.7%** | 4 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **history.py**:
  - `_i`, `_ii` and `_iii` are empty before the first cell, even when `In[0]` holds text, and `_iii` is three cells back.
  - `%reset -f` removes each numbered result, and works when there is no `Out` yet.
  - A range such as `2-3` lists each cell in it.
  - A cell number past the last one is skipped.
  - → `test_history_mutation.py`.
- **artifacts.py**:
  - Which types have no definition API, and which Fabric Git and deployment pipelines mark preview, are pinned as sets. These come from the Microsoft lists in `SOURCES`, so a change to a list means updating the registry and the test together.
  - Only Notebook, DataPipeline and Warehouse round-trip.
  - A capability can't be changed once made.
  - → `test_artifacts_mutation.py`.
- **preview.py**:
  - The limits are 100 rows, 200 MiB and 20,000 characters, and a CSV shows 100 rows by default.
  - Dates and decimals show as text, and NaN as its name.
  - A CSV is read only until it has enough rows: a reader handing out batches of 5 is stopped after the batch that reaches the limit.
  - A text file of exactly the limit is not marked as cut.
  - → `test_preview_mutation.py`.

### Equivalent mutants left

- **history.py**:
  - Line 23, `len(inputs) < n` to `<=`: the extra entry is replaced by `inputs[n:] = [code]`, so `In` ends the same.
  - Line 85, the end of a single-number range read from the whole match instead of its first group: for a single number they are the same text.
  - Line 94, the default range starting at 0 instead of 1: cell 0 is skipped by the `0 < n` check below.
- **preview.py**:
  - Line 45, `zip(..., strict=True)` to `False`: the columns of one table always have the same length.
  - Line 64, `flatten=True` to `False`: with the pinned deltalake, `num_records` is a top-level column either way.
  - Line 65, converting the add actions to a pyarrow table only when they aren't one, negated: the pinned deltalake's table has `.column(...).to_pylist()` too, so the count is the same.
  - Line 74, the file name split from the path with `rsplit("/", 0)`: an extension taken from a folder name always contains `/`, so it matches no known kind.

# Mutation testing, batch 55

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The setup an AI tool needs: the launcher names and scripts, and the configuration snippets for each tool (`integrations/config.py`).
- The scheduled shortcut health check: probing each shortcut, scanning an environment's workspaces, and which agent posts (`shortcut_watch.py`).
- The contract every execution engine adapter meets: its limits and the request, config and result shapes (`engines/contract.py`).

The run was at `main` babc8ef32 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. Five tests in `test_integrations_prompts.py` and `test_vscode_setup.py` compare against pages in `docs/`, so the `integrations/config.py` runs leave them out with `-k "not (agent_instructions_name or connect_page_carries or kratosforge_skill_names or workflows_page_matches or copilot_instructions_match)"`. The new tests check the snippets directly instead.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/config.py` | test_integrations_permissions, test_integrations_prompts, test_vscode_setup, with the `-k` above (+ test_integrations_config_mutation) | 30 of 30 (30 + 0) | 19 / 11 | **63.3%** | 19/30 → 30/30 | none | 30 / 0 | **100%** | none |
| `shortcut_watch.py` | test_shortcut_watch, test_agent_mutation (+ test_shortcut_watch_mutation) | 32 of 32 (31 + 1) | 25 / 7 | **78.1%** | 25/31 → 31/31 | 0/1 → 0/1 | 31 / 1 | **96.9%** | 1 equivalent |
| `engines/contract.py` | test_engines, test_engine_conformance, test_engine_compare (+ test_engine_contract_mutation) | 14 of 14 (14 + 0) | 5 / 9 | **35.7%** | 5/14 → 14/14 | none | 14 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/config.py**:
  - The launchers are `fabric-desktop-mcp.cmd` and `kratosforge.cmd` on Windows, and the same names without `.cmd` elsewhere.
  - The script form follows the system it runs on unless asked for the other.
  - A launcher folder that doesn't exist yet is made.
  - The JSON snippets are two-space JSON naming the client, and the HTTP snippet is there only when an address is given.
  - → `test_integrations_config_mutation.py`.
- **shortcut_watch.py**:
  - The check uses a pool of 8 workers.
  - A folder is listed once and its answer reused for each shortcut in it.
  - A folder that answers 401 or 403 checks none of its shortcuts, and each is marked not checked.
  - A lakehouse whose shortcuts can't be listed is named in the problem, by display name or else id.
  - An environment's own workspace is kept beside its designations.
  - → `test_shortcut_watch_mutation.py`.
- **engines/contract.py**:
  - The preview is 1000 rows and the largest result table 256 MiB.
  - A session config and a cell request can't be changed once made.
  - A result's table is left out of its text, its comparisons and what goes to the app.
  - Seconds are given to four places.
  - → `test_engine_contract_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `shortcut_watch.py` | 120 | `o < me` to `o <= me` | Equivalent: the names compared leave out every record naming `me`, so no name in the list can equal it. |

# Mutation testing, batch 56

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What each agent publishes about itself: the status record, its incident and shortcut summaries, and writing `agents/<name>.json` (`agent_status.py`).
- The semantic model metadata App Studio may describe: tables, columns, measures and relationships, with caps and cleaned text (`appstudio/discovery.py`).
- Reading a Dataflow Gen2 definition: splitting the M section into statements, each query's attributes, and where it loads (`orchestration/mashup.py`).

The run was at `main` 7bd752a5d (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used. `orchestration/mashup.py` has 168 mutants, so the cap of 150 applies; six of those hang (a scanner that no longer moves forward) and count as caught, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent_status.py` | test_agent_status, test_agent_fleet, test_agent_silent_failures, test_shared_schedules, test_run_as, test_agent_takeover, test_schedule_overlap (+ test_agent_status_mutation) | 40 of 40 (40 + 0) | 21 / 19 | **52.5%** | 21/40 → 40/40 | none | 40 / 0 | **100%** | none |
| `appstudio/discovery.py` | test_appstudio_discovery, test_appstudio_discovery_edges, test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_generate (+ test_appstudio_discovery_mutation) | 90 of 90 (85 + 5) | 79 / 11 | **87.8%** | 75/85 → 85/85 | 4/5 → 5/5 | 90 / 0 | **100%** | none |
| `orchestration/mashup.py` | test_workspace_dataflows (+ test_mashup_mutation) | 150 of 168 (140 + 10) | 112 / 38 | **74.7%** | 111/140 → 136/140 | 1/10 → 6/10 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **agent_status.py**:
  - A record reads up to 200 of the environment's jobs and lists the 10 newest finished, newest first; a job with no finish time sorts as the oldest.
  - A job's error is cut to 300 characters, and an empty one reads as none.
  - The pipeline shows as enabled only when it is on and serves this environment.
  - Counters an older agent leaves out read as 0, and the waiting count as unknown.
  - The incident summary keeps each route but not the name of its webhook variable, and gives its tuples as lists.
  - Shortcut health is on only when enabled for this environment.
  - `publish` writes two-space JSON under `agents/`, replaces with the current etag, and gives up after three tries.
  - → `test_agent_status_mutation.py`.
- **appstudio/discovery.py**:
  - The caps (300 tables, 4000 columns, 2000 measures, 1000 relationships) and text limits (128, 300, 2000 characters) are pinned, and exactly each cap is read whole while one more is cut and marked.
  - Text of exactly its limit is kept whole; longer text is cut to the limit with an ellipsis.
  - Table, column and measure descriptions leave out emails and user names in paths.
  - A measure expression is cleaned of credential-shaped text only, and otherwise kept as written.
  - → `test_appstudio_discovery_mutation.py`.
- **orchestration/mashup.py**:
  - Statements split from the first character, and blank text after the last semicolon isn't a statement.
  - A semicolon inside a string, a line comment, a block comment or brackets doesn't split; doubled quotes stay in a string, and a stray close bracket doesn't go below zero.
  - Leading spaces and comments are skipped before a query's attributes, and an unclosed comment leaves nothing.
  - The attribute record ends at its own close bracket, with brackets and quotes inside strings (including an empty string and doubled quotes) read as text; an unclosed record or string has no attributes.
  - Quoted query names keep doubled quotes, and what isn't `shared` is skipped.
  - A destination reads its target query's kind, workspace, item, schema, table, update method and new-table flag; an unknown function reads as "Unknown" and a missing query as nothing.
  - Connection ids come from JSON or plain text, and a query loads and shows unless its flags say otherwise.
  - → `test_mashup_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `orchestration/mashup.py` | 51 | `end < 0` to `end < 1`, and to `end <= 0` | Equivalent: a line comment starts with `//` at `i`, so the newline found from `i` is never at 0. |
| `orchestration/mashup.py` | 54 | `end < 0` to `end <= 0` | Equivalent: the close is searched from `i + 2`, so it is never found at 0. |
| `orchestration/mashup.py` | 73 | `end <= 0` to `end <= 1` | Equivalent: the text starts with `/`, so a newline or `*/` is never found at 0, and `end` is never 1. |
| `orchestration/mashup.py` | 86 | `i >= 0` to `i >= 1`, and to `i > 0` | Equivalent: the closing quote is searched from after the opening one, so it is never found at 0. |
| `orchestration/mashup.py` | 86 | `i >= 0` to `i < 0` (the doubled-quote loop never runs) | Equivalent: without the loop, the second quote of a pair opens a new string at once, and the scan ends at the same place. |
| `orchestration/mashup.py` | 88 | `i < 0` to `i <= 0` | Equivalent: as on line 86, a found quote is never at 0. |

# Mutation testing, batch 57

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- `%autoreload` and `%aimport` on this PC: the modes, which modules are looked at, and reloading a changed one (`autoreload.py`).
- Profile: column statistics and the clean and check rules suggested for a table (`profiling.py`).
- Try the fix beside a SQL best-practice finding: where a finding points, the rewrite and the reads Compare runs (`learn/fixes.py`).

The run was at `main` d37fd8ce8 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. `profiling.py` has 196 mutation points and `learn/fixes.py` 261, so 150 of each were sampled. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `autoreload.py` | test_autoreload_edges, test_notebook_autoreload (+ test_autoreload_mutation) | 70 of 70 (69 + 1) | 54 / 16 | **77.1%** | 54/69 → 67/69 | 0/1 → 1/1 | 68 / 2 | **97.1%** | 2 equivalent |
| `profiling.py` | test_profiling_edges, test_cloud (+ test_profiling_mutation) | 150 of 196 (138 + 12) | 88 / 62 | **58.7%** | 86/138 → 136/138 | 2/12 → 11/12 | 147 / 3 | **98.0%** | 3 equivalent |
| `learn/fixes.py` | test_learn_fixes, test_learn_fixes_edges (+ test_learn_fixes_mutation) | 150 of 261 (138 + 12) | 120 / 30 | **80.0%** | 116/138 → 130/138 | 4/12 → 8/12 | 138 / 12 | **92.0%** | 12 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **autoreload.py**:
  - Each mode word (`0`, `off`, `1`, `explicit`, `2`, `all`, `3`, `complete`) sets its mode.
  - A new reloader is off, so even a changed module named with `%aimport` isn't reloaded until a mode is set.
  - `%aimport` remembers a module's time, and a module whose time hasn't moved since is not reloaded; one that changed is reloaded in mode 1.
  - Off answers with an empty list.
  - Skipped modules, `__main__` and empty `sys.modules` entries are never reloaded.
  - `%aimport` with no names lists both sets, sorted, with "none" for an empty one.
  - A reload that fails prints the frame where it failed, and the cell still runs.
  - → `test_autoreload_mutation.py`.
- **profiling.py**:
  - The TOML a suggestion carries writes true and false, numbers, inline tables, lists and quoted text.
  - The limits are 12 accepted values and 5 top values, and the top values are the most common with their counts.
  - An all-null column has no distinct values or top values; the null share is a percent to one place, and an empty table has none.
  - A clean suggestion has no `on_fail`, and a check warns by default.
  - A rule already in place isn't suggested again, and the issue says a clean rule handles it.
  - Case is lower when exactly half the values are lower case, and the note counts the extra spellings.
  - Accepted values need 2 to 12 values and 3 rows for each.
  - Far outliers lie beyond three spreads of the middle half, exactly at a fence is not one, and there are none when the middle half has no spread or the column is key-like.
  - A range needs 3 values, none below 0; a few negatives are noted up to 1 in 100.
  - Date and date-and-time columns show their first and last values.
  - A named key must not be null and must be unique, and stops the run; an `_id` column with no keys named gets warning checks; unique isn't suggested for one row, beside another named key, or for a name not ending `_id`.
  - Repeated values are noted only for a named key, and a few nulls up to 1 in 100.
  - → `test_profiling_mutation.py`.
- **learn/fixes.py**:
  - A finding on line 1, or in the first column of a later line, gets its fix; one on line 0 gets none.
  - `YEAR` at the end of a statement, without `= <year>` after it, or followed by one more operator is left alone; followed by `AND` it is fixed.
  - The reason names the column, not its qualifier, and a column named with three parts is left alone.
  - `FROM` as the last word isn't read as a table, a table at the very end of the statement is read, and two tables after one `FROM` are left alone even with aliases.
  - SQ005 puts `dbo.` before the name as written, brackets included, and says why.
  - Compare runs a view's query, isn't offered for CREATE TABLE ... AS SELECT or a view with nothing after `AS`, and leaves out a closing semicolon.
  - → `test_learn_fixes_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `autoreload.py` | 63, 101 | `force_mode=2` and `_candidates(2)` to 3 | Equivalent: `_candidates` only tells mode 1 apart, so modes 2 and 3 pick the same modules. |
| `profiling.py` | 68 | `return False` to `return None` | Equivalent: the answer is only read as true or false. |
| `profiling.py` | 105 | `folded` for no real values 0 to 1 | Equivalent: with no real values, `folded` is only read by `1 < folded`, false for 0 and 1. |
| `profiling.py` | 165 | `nulls < n` to `nulls <= n` | Equivalent: when every value is null the null share is 100%, so `null_pct <= 1` is already false. |
| `learn/fixes.py` | 89 | `return -1` to `return None` | Equivalent: no token starts at -1 or None, so the finding gets no fix either way. |
| `learn/fixes.py` | 88 | `col < 1` to `col < 0` | Equivalent: column 0 points at the line break before the line or before the query, where no token starts. |
| `learn/fixes.py` | 108 | `_close(s, i + 1)` to `_close(s, i)` | Equivalent: `YEAR` and its `(` have the same depth, so the same `)` is found. |
| `learn/fixes.py` | 189 | two moves in the bracketed-schema branch | Equivalent: SQ005 only fixes names in `dbo`, which needs no brackets, so the branch never runs. |
| `learn/fixes.py` | 203 | `len(toks) > 2` to `> 3` and to `>= 2` | Equivalent: `CREATE VIEW` or `CREATE VIEW v` has no `AS`, so Compare is not offered either way. |
| `learn/fixes.py` | 210 | `t.start >= start` to `>` | Equivalent: the token at `start` is `SELECT` or `WITH`, never a write. |
| `learn/fixes.py` | 213, 214 | the closing-semicolon check moved or negated | Equivalent: the statement splitter leaves the `;` out of a statement's tokens, so there is never one to trim. |
| `learn/fixes.py` | 215 | `start <= fix start` to `<`, and `fix start < fix end` to `<=` | Equivalent: a fix never starts on the `SELECT` or `WITH` the read starts at, and is never empty. |

# Mutation testing, batch 58

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- `%pip` in a notebook cell on this PC: the commands it answers itself, an install followed through the engine, and how a failure is shown (`pip_magic.py`).
- The headless agent's JSON log lines and its native crash report (`logs.py`).
- OneLake shortcuts: how one is described, where it may go, its target, its name, and which managed tables use it (`shortcuts.py`).

The run was at `main` 346098b16 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `pip_magic.py` | test_pip_magic_run, test_notebook_magics_windows, test_environments, test_builtin_links (+ test_pip_magic_mutation) | 70 of 70 (69 + 1) | 61 / 9 | **87.1%** | 61/69 → 68/69 | 0/1 → 1/1 | 69 / 1 | **98.6%** | 1 equivalent |
| `logs.py` | test_logs_json, test_agent_crash_report, test_agent_clock_and_folder (+ test_logs_mutation) | 34 of 34 (33 + 1) | 29 / 5 | **85.3%** | 29/33 → 33/33 | 0/1 → 1/1 | 34 / 0 | **100%** | none |
| `shortcuts.py` | test_shortcut_center, test_shortcut_request_retries, test_shortcut_service_paths (+ test_shortcuts_mutation) | 85 of 85 (85 + 0) | 72 / 13 | **84.7%** | 72/85 → 85/85 | none | 85 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **pip_magic.py**:
  - An install is followed for 16 minutes, a minute past the engine's own limit.
  - A failure shows exactly the last 4000 characters of pip's log.
  - Reaching the deadline exactly still polls once more; only past it gives up.
  - `%pip show` prints an empty summary when a package has none.
  - → `test_pip_magic_mutation.py`.
- **logs.py**:
  - An object written as text is cleaned too.
  - The crash log is set aside past one mebibyte, and a log of exactly the limit is kept.
  - The crash log's folder is made with its parents, and every thread is traced.
  - → `test_logs_mutation.py`.
- **shortcuts.py**:
  - A folder shortcut is used by tables that read files under it, not by one whose folder only shares the start of its name, nor by the folder itself.
  - A table shortcut clashes only with a table of the same schema and name.
  - A shortcut's kind comes from its path, and its target type from the target, with "Other" when there is none.
  - Names with other characters (`\ / : * ? " < > |`), an empty name and one over 256 characters are refused; 256 is allowed and the name is trimmed.
  - → `test_shortcuts_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `pip_magic.py` | 60 | `re.split(..., maxsplit=1)` to `maxsplit=0` | Equivalent: only the first piece (`[0]`) is kept, and splitting at every comment marker leaves the text before the first one the same. |

# Mutation testing, batch 59

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- How much memory is free for a local query, a container's limit included, and the budget a query gets from it (`free_memory.py`).
- Reading a row of a Capacity Metrics app export: the reasons a row is skipped, the period it covers and how it is keyed (`usage/metrics_app.py`).
- The workloads in a workspace for the optimization engine: the summary of an item's job history and reading a notebook's cells from its definition (`opportunities/inventory.py`).

The run was at `main` 732f1b5ad (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/` (`test_opportunities_assess_mutation.py` does, so it was not used), and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `free_memory.py` | test_free_memory, test_free_memory_edges, test_meta_preflight_mutation (+ test_free_memory_mutation) | 53 of 53 (51 + 2) | 47 / 6 | **88.7%** | 47/51 → 51/51 | 0/2 → 2/2 | 53 / 0 | **100%** | none |
| `usage/metrics_app.py` | test_usage_import (+ test_usage_metrics_app_mutation) | 53 of 53 (51 + 2) | 34 / 19 | **64.2%** | 34/51 → 51/51 | 0/2 → 2/2 | 53 / 0 | **100%** | none |
| `opportunities/inventory.py` | test_opportunities, test_opportunities_analyzers_mutation (+ test_opportunities_inventory_mutation) | 84 of 84 (81 + 3) | 68 / 16 | **81.0%** | 67/81 → 80/81 | 1/3 → 3/3 | 83 / 1 | **98.8%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **free_memory.py**:
  - A control-group path may hold a colon.
  - On cgroup v1, a limit of exactly 2^60 means no limit, and one less is a limit.
  - When three quarters of the free memory equals the share asked for, the share sets the cap; a share between three quarters of free and all of it is cut to three quarters.
  - → `test_free_memory_mutation.py`.
- **usage/metrics_app.py**:
  - Each reason a row is skipped, read one row at a time: no workspace or item, no start time, a start or end time that can't be read, an end before the start, a negative CU value.
  - A workspace or an item alone is enough, zero and fractions of a CU second are kept, and an operation may end as it starts.
  - An operation covers its start to its end, or just its start; an item total needs both ends of its period and covers it.
  - An operation is keyed by its id, or else by what it ran; an item total by its period; the hash part of a key is 24 characters.
  - Optional fields are kept when given and empty when not.
  - → `test_usage_metrics_app_mutation.py`.
- **opportunities/inventory.py**:
  - Runs exactly at either edge of the window count, and one outside does not; a start that isn't text is skipped.
  - A time's offset is kept, and a time without one is read as UTC.
  - Only finished runs with an end not before their start are timed, a run of 0 seconds included; only failed runs count as failed.
  - Seconds are rounded to three places; no runs is 0 seconds, and runs none of which finished is unknown.
  - The last run is the latest one.
  - A payload that isn't base64 is read as it is, a cell's source given as lines is joined, and a definition without notebook content is refused.
  - → `test_opportunities_inventory_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `opportunities/inventory.py` | 110 | `zip(..., strict=False)` to `strict=True` | Equivalent: splitting on n cell markers gives n + 1 chunks, so the markers and `chunks[1:]` always have the same length. |

# Mutation testing, batch 61

Four modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Job files and running a job on this PC: loading, the name and engine checks, and reading the result as Arrow (`jobs.py`).
- Measuring a run: wall time, CPU time and memory, on the engine or a notebook's own process (`measure.py`).
- This installation's id for the optimization tables: the id file, its label and a new id on reset (`optimize/installation.py`).
- Home's Ready to work checklist: the clock, free space, network drive and workspace rows, and the write test's time (`readiness.py`).

The run was at `main` 5d5028f98 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Three test files were left out:
- `test_local.py` reads `examples/`.
- `test_clock_skew.py` reads `app/`, outside `sidecar/`. A first `readiness.py` run that included it scored a false 94 of 94, because the file can't be collected in the copy. That run was thrown away and the module was run again without it.
- `test_kernel_process.py` and `test_sql_exec.py` reach `measure.py` but take 35 to 50 seconds each. They were left out for time, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `jobs.py` | test_jobs_edges, test_append_callers, test_duckdb_try_cast, test_environment_isolation, test_publish_edges, test_publish_mutation (+ test_jobs_mutation) | 32 of 32 (32 + 0) | 22 / 10 | **68.8%** | 22/32 → 32/32 | none | 32 / 0 | **100%** | none |
| `measure.py` | test_measure_meter, test_optimize_local_runs, test_engine_usage_mutation, test_scorecard_mutation (+ test_measure_mutation) | 68 of 68 (67 + 1) | 54 / 14 | **79.4%** | 54/67 → 67/67 | 0/1 → 0/1 | 67 / 1 | **98.5%** | 1 equivalent |
| `optimize/installation.py` | test_optimize_service, test_optimize_service_edges, test_corrupt_state_reads, test_opportunities_share, test_optimize_shared (+ test_optimize_installation_mutation) | 19 of 19 (19 + 0) | 11 / 8 | **57.9%** | 11/19 → 19/19 | none | 19 / 0 | **100%** | none |
| `readiness.py` | test_readiness, test_offline_start, test_service_edges (+ test_readiness_mutation) | 94 of 94 (89 + 5) | 52 / 42 | **55.3%** | 52/89 → 88/89 | 0/5 → 4/5 | 92 / 2 | **97.9%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **jobs.py**:
  - Loading a job file gives the job, with its folder and target.
  - A job name that is empty, only spaces, `.`, `..` or `_writes`, or that holds `/`, `\` or `:` (`../x`, `a/b`, `a\b`, `c:x`), is refused. `sales`, `a.b`, `...` and `_write` are accepted.
  - An unknown engine is refused.
  - A job and its target can't be changed once made.
  - The result is read with whichever Arrow method DuckDB has, `to_arrow_table` or `fetch_arrow_table`, after setting the time zone to UTC.
  - → `test_jobs_mutation.py`.
- **measure.py**:
  - On Windows, CPU time joins the high and low halves of each time, and the process handle isn't inherited.
  - The meter is its own context and samples on a daemon thread named `run-meter`, with no samples counted before the interval passes.
  - On leaving, the sampler is stopped and waited for up to 2 seconds before the last read.
  - Engine wall and CPU times are differences, rounded to milliseconds; a kernel's CPU time is too.
  - Memory is reported to a tenth of a MB.
  - → `test_measure_mutation.py`.
- **optimize/installation.py**:
  - The time stamp is UTC to the second.
  - The OS family is macOS, Windows or Linux, or "unknown" when Python can't tell.
  - The file is written with one-space indents.
  - A label keeps its first 80 characters, trimmed.
  - A file that can't be read counts as no file.
  - Reset gives the new id and keeps the label.
  - Only a lowercase UUID counts as an id.
  - → `test_optimize_installation_mutation.py`.
- **readiness.py**:
  - The limits are 5 minutes and 2 GiB, and a network drive is drive type 4.
  - A clock exactly 5 minutes off either way is ok, and a row without a host names "the server".
  - Further off, the row gives minutes up to 119, then hours, rounded, and says ahead or behind.
  - One workspace is singular, and none offers the workspaces page.
  - Exactly 2 GiB free is enough, one byte less warns, and the amount is given in GiB.
  - On Windows, a drive letter path, or a drive alone such as `Z:`, is asked about by its root. A path without a drive letter isn't asked about, a drive that can't be asked about counts as on this PC, and off Windows no drive is asked about.
  - The write test reports how long it took.
  - → `test_readiness_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `measure.py` | 119 | `now > self.peak` to `>=` | Equivalent: when the new reading equals the peak, setting the peak to it changes nothing. |
| `readiness.py` | 121 | `seconds > 0` to `>= 0` | Equivalent: the row is only worded for a clock more than 5 minutes off, so `seconds` is never 0 there. |
| `readiness.py` | 121 | `seconds > 0` to `seconds > 1` | Equivalent for the same reason: no `seconds` between 0 and 1 reaches that line. |

# Mutation testing, batch 60

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Whether a notebook should run on this PC or in Fabric, and the reasons given for it: the compatibility check, the proof, a side-by-side run and the Fabric job CU (`advisor.py`).
- Pairing a VS Code extension with the app: the code shown to the person, how long a request waits, how many may wait, and the token a paired extension gets (`editor_pairing.py`).
- Help while typing in a notebook cell: the name under the cursor, the call being typed and the first syntax error in a cell (`intellisense.py`).

The run was at `main` a1f7002ff (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `intellisense.py` has 188 mutants, so 150 were sampled; the other two modules were run in full.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used. The `editor_pairing.py` "after" run left out `test_editor_channel.py` (about 27 seconds) to keep the run short; adding a test file can only kill more mutants, so the score with it is the same or higher.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `advisor.py` | test_advisor, test_compute_check_ast, test_risk_service_edges (+ test_advisor_mutation) | 127 of 127 (126 + 1) | 101 / 26 | **79.5%** | 101/126 → 126/126 | 0/1 → 1/1 | 127 / 0 | **100%** | none |
| `editor_pairing.py` | before: test_editor_channel, test_editor_pairing_edges, test_editor_service_edges; after: test_editor_pairing_edges, test_editor_service_edges (+ test_editor_pairing_mutation) | 99 of 99 (96 + 3) | 83 / 16 | **83.8%** | 81/96 → 96/96 | 2/3 → 3/3 | 99 / 0 | **100%** | none |
| `intellisense.py` | test_intellisense, test_intellisense_edges (+ test_intellisense_mutation) | 150 of 188 (140 + 10) | 112 / 38 (4 timeouts counted as killed) | **74.7%** | 108/140 → 134/140 | 4/10 → 8/10 | 142 / 8 (4 timeouts counted as killed) | **94.7%** | 8 equivalent |

Two bugs were found, both in `intellisense.py`, and both are now fixed. When a syntax error covers more than one line, the end column reported for it was clamped as if the error ended on its first line (#2743). And a `%%sql` cell whose first line holds only spaces was checked as Python, because only empty lines were skipped before looking for the cell magic, so it got a false syntax error (#2746; the line 165 mutant, first listed as equivalent, found it in review). Both are fixed, in #2745 and #2747, and `test_intellisense_mutation.py` checks the right answer for each; the #2746 test also kills the line 165 mutant.

### What the survivors were, and the tests that now kill them

- **advisor.py**:
  - Up to three cells are listed by number, more are summed up as "and N more", and a reason with no cell has no cell prefix.
  - When the check can't read a cell, the reason says it is unknown whether the notebook runs on that side, names the cell, and is marked unknown.
  - The Fabric job CU is unknown when its classification is unknown or it has no value, with the recorded reason or "not recorded"; the label is shown in lower case, or the classification when there is no label.
  - The proof's text falls back from its text to its verdict to "Proved.".
  - The exact wording of a side-by-side run for one table, for two, for none and for a failed run.
  - A side-by-side run of other code, or of code that wasn't recorded, is shown as context with its verdict or "no verdict"; when a proof exists, no side-by-side run is used.
  - A failed proof asks the person to look at its result and prove the notebook again.
  - → `test_advisor_mutation.py`.
- **editor_pairing.py**:
  - The three ways to keep a pairing and their lengths in seconds, how long a request waits, and how many may wait at once.
  - The code shown to the person is two groups of three letters or digits, and request, pairing and token ids have their exact form and length.
  - A folder that is missing, empty, not text, or holds a control character is refused.
  - The name an extension gives is cut to 80 characters and cleaned of control characters, with a default when it gives none.
  - A fourth request is refused while three wait, and a denied request frees a place.
  - A request is still waiting at exactly 120 seconds and gone one second later.
  - Each answer to a request is collected once; a keep that isn't one of the three, and a request that is unknown or already answered, are refused.
  - A token that is missing, not text or of the wrong form is refused, and a token works until the moment it expires.
  - The saved pairings must be a list, and entries that aren't records with a text hash are dropped.
  - Ending every pairing writes no file when none existed.
  - → `test_editor_pairing_mutation.py`.
- **intellisense.py**:
  - The name under the cursor at the start and end of a line; a doc is cut at 1500 characters; a callable object is shown as a function.
  - Comments and triple-quoted strings are skipped when finding the call being typed; the innermost open call and the keyword being typed are found.
  - Past the positional parameters, a `*args` parameter is the active one; the return type is shown when there is one; a doc in call help is cut at 400 characters.
  - Line numbers after a `%%python` cell magic, on the first line or after blank lines, and a line magic replaced so the error is reported on the right line.
  - The exact span of an error on one line and over several lines, of a null byte, and of an error at column 0.
  - The check is compiled without the module's own future imports, so `yield` in an annotation is an error.
  - A message is capitalised, with "Invalid syntax" when there is none.
  - → `test_intellisense_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `intellisense.py` | 87 | `nl < 0` to `nl <= 0` | Equivalent: the search for the end of a comment starts at the `#`, so it can't find a line break at index 0. |
| `intellisense.py` | 171 | `count("\n") + 1` to `+ 0` | Equivalent: the cell magic line is then kept and replaced by `pass`, so the reported line numbers come out the same. |
| `intellisense.py` | 178 | `max(0, ...)` to `max(1, ...)`, `- 4` to `+ 4`, `- 4` to `- 5` | Equivalent (3 mutants): these change only the spaces after `pass` at the end of a line, which never change a syntax error. |
| `intellisense.py` | 192 | `col + 2` to `col - 2` | Equivalent: with no end offset, the larger of the two is `col + 1` either way. |
| `intellisense.py` | 193 | `end_line == err_line` to `!=`, `end_col <= col` to `end_col < col` | Equivalent (2 mutants): the end column is always past the start column, so that part of the test is never true. |

These rows describe the code before #2745 and #2747; on today's code line 171 is gone and the line 193 mutants are killed by the existing tests.

# Mutation testing, batch 62

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The Fabric step: the Run on Fabric mark, table names for V-Order, reading what a cell OPTIMIZEs, and the one-cell Spark notebook (`fabric_step.py`).
- Script activities in local pipeline runs: lakehouse or local data project, the checks before the run, running each block and reading its rows (`pipeline_script.py`).
- Schedules that also run while you're signed out of Windows: the schedule id check, the PowerShell call, Task Scheduler's results and the log line (`signed_out_tasks.py`).

The run was at `main` ae262ce70 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. No test file that reads `docs/`, `examples/` or `app/` was run. Some test files were left out:
- `test_pipeline_runs.py` reaches `pipeline_script.py` but takes about 20 seconds a run. A first run that included it was stopped and the module was run again without it.
- `test_side_by_side.py`, `test_side_by_side_edges.py`, `test_notebook_cells.py`, `test_notebook_magics.py`, `test_risk.py`, `test_risk_service_edges.py`, `test_validate.py` and `test_integrations_executions.py` reach `fabric_step.py` or `signed_out_tasks.py` only through other modules. They were left out for time, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fabric_step.py` | test_fabric_step (+ test_fabric_step_mutation) | 89 of 89 (85 + 4) | 81 / 8 | **91.0%** | 77/85 → 85/85 | 4/4 → 4/4 | 89 / 0 | **100%** | none (scored before #2759; see below) |
| `pipeline_script.py` | test_pipeline_script, test_pipeline_script_edges (+ test_pipeline_script_mutation) | 147 of 147 (144 + 3) | 110 / 37 | **74.8%** | 108/144 → 142/144 | 2/3 → 3/3 | 145 / 2 | **98.6%** | 2 equivalent |
| `signed_out_tasks.py` | test_signed_out_schedules, test_local_schedules_edges (+ test_signed_out_tasks_mutation) | 87 of 87 (86 + 1) | 68 / 19 | **78.2%** | 68/86 → 86/86 | 0/1 → 1/1 | 87 / 0 | **100%** | none |

One bug was found, in `fabric_step.py`, and filed as #2749: a `%sql` line magic below the cell's first line (`df = %sql SELECT 1`) was refused with "isn't Python that Fabric Spark can run", though the docstring says `%sql` lines become `spark.sql` as when publishing. Nothing ran, so the refusal was safe. #2759 fixed it, and `test_a_sql_line_magic_below_the_first_line_becomes_spark_sql` now passes as a plain test.

After #2759, the module has 90 mutants (86 + 4), and the same test files kill 89 of them (**98.9%**). The one left turns `language == "sql"` into `!= "sql"` at line 166. It is equivalent: a cell whose language is `sql` always starts with `%` once leading blank lines are stripped, so the next test in that `if` catches it anyway, and a cell with no magics comes through the `%sql` rewrite unchanged. Cells with string literals holding `%%sql`, comments, `!` lines, other magics, indented `%sql` lines and leading blank lines all give the same result either way.

### What the survivors were, and the tests that now kill them

- **fabric_step.py**:
  - An empty marked cell is named as empty, whether it is last or has code below it.
  - Two code cells below the mark are counted as "2 code cells are below".
  - A table name has one or two parts, each quoted with backticks (a backtick inside is doubled). Empty parts are skipped, and no parts or three parts are refused.
  - A table OPTIMIZEd twice is listed once.
  - A bare `%sql` line on the first line and an indented `%%sql` cell both become `display(spark.sql(...))`, and a Python cell is left as written.
  - A syntax error names its line.
  - The step notebook's header names the notebook exactly, and the result goes to the request's file in the attached lakehouse.
  - → `test_fabric_step_mutation.py`.
- **pipeline_script.py**:
  - An expression is a string starting with `@` (not `@@`) or a dict of type `Expression`. Only an expression's text is unwrapped.
  - Projects keep only text names that map to text projects.
  - A Script activity reads a lakehouse when its connection is a `Lakehouse` and no project is named for the activity or its connection.
  - A connection with no type is called "a connection", and a named type is named.
  - Every message names the script it's about by its number (parameters, unreadable T-SQL, a file read, a confirm, a refusal, a time limit, a stop or a failure), and a file read names its statement by number too.
  - A refusal without a label or reason still reads ("is refused (not allowed here)").
  - A failed script says the refusal's message, else the error, else "failed", with the matching error code.
  - A person cancelling a running script fails it as cancelled.
  - Lakehouse translation notes are logged under their script's number.
  - Later pages ask only for the rows still wanted, a longer page is cut at 5,000 rows, and a row that doesn't match the columns fails.
  - At exactly the deadline a script keeps running; only a later clock reading ends it with the policy timeout (added in review).
  - → `test_pipeline_script_mutation.py`.
- **signed_out_tasks.py**:
  - A schedule id is 1 to 32 lowercase hex characters and is returned as given. `../x`, `a/b`, `a\b`, an empty or missing id, uppercase, 33 characters, a leading or trailing space, a trailing newline, `;`, `'`, a space inside and `0x1f` are refused.
  - PowerShell is called by its full path under `SystemRoot` (or `C:\Windows`), with exact arguments: `-NonInteractive` and a 2-minute limit by default, no `-NonInteractive` and a 5-minute limit for the password prompt. Output is captured as text, and a non-zero exit is returned, not raised.
  - The exit codes the scripts use are the ones the Python side reads.
  - The task definition is written as UTF-16, handed over by environment, and removed afterwards, also when the prompt times out.
  - Pausing or resuming passes the choice, succeeds on exit 0 and raises with Task Scheduler's words on any other exit. A bad id is refused before anything runs.
  - The log line replaces the account name in any case, but not a one-letter name. It is one line, cut at 500 characters, and a refusal is logged without the account name.
  - A task's last result reads "password" or "not allowed" for both forms of each code, and anything else reads as nothing.
  - → `test_signed_out_tasks_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `pipeline_script.py` | 159 | `"resultSetCount": 0` to `1` | Equivalent: every path that returns `out` sets `resultSetCount` to the number of result sets first (line 175). |
| `pipeline_script.py` | 204 | `return None, False` to `None, True` | Equivalent: `run` reads the cut flag only when there are rows (`rows is not None`), and here there are none. |

None of the three involves text, so leading or trailing whitespace and regex edge cases can't change them.

# Mutation testing, batch 63

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Charts from bokeh, altair and plotly in a cell's output: how each library's own show function is replaced, the Vega-Lite size limit, and the import hook that patches a library as it loads (`chart_output.py`).
- Which Fabric capacity each environment's workspace is on, how a failed read is classified, and how old a cached reading is (`capacity.py`).
- The accomplishments report as a PDF: font widths, line wrapping, page breaks, layout positions and the file structure (`pdfreport.py`).

The run was at `main` 41988f99a (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `pdfreport.py` has 317 mutants, so 150 were sampled; the other two modules were run in full.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/`, `examples/` or `app/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `chart_output.py` | test_chart_libraries (+ test_chart_output_mutation) | 73 of 73 (72 + 1) | 41 / 32 | **56.2%** | 41/72 → 72/72 | 0/1 → 1/1 | 73 / 0 | **100%** | none |
| `capacity.py` | test_capacity, test_retrying (+ test_capacity_mutation) | 88 of 88 (87 + 1) | 74 / 14 | **84.1%** | 74/87 → 87/87 | 0/1 → 1/1 | 88 / 0 | **100%** | none |
| `pdfreport.py` | test_profile_goals_impact (+ test_pdfreport_mutation) | 150 of 317 (146 + 4) | 37 / 113 | **24.7%** | 37/146 → 145/146 | 0/4 → 4/4 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **chart_output.py**: the tests load a separate copy of the module and use stand-in library modules, so the engine's own copy and its import hook are not changed.
  - Before `install()`, nothing is drawn and no cell counts as running; `install()` puts its import hook first in the import path, and only once.
  - An Altair spec exactly at the 20,000,000-character limit is drawn, and one character more gives the "too large" text with its size.
  - A plotly figure dict with a property plotly doesn't know is still drawn, unchanged.
  - A bokeh figure is recognised and gives an HTML page titled "Chart"; a number, or an object from a bokeh module that isn't a bokeh model, is not.
  - An object with `to_plotly_json` from outside plotly doesn't count as a plotly figure.
  - Drawing outside any cell returns False.
  - bokeh's show is replaced once, keeps its docstring, and is replaced only in the modules where it is bokeh's own; the replacement draws in a cell without calling bokeh, refuses in a thread a cell started, and otherwise calls bokeh's show with the same arguments and returns its result.
  - Altair's show is replaced once and keeps its docstring; outside a cell it calls Altair's show with the same arguments, and in a cell it draws the spec; an Altair module without the mixin is left alone.
  - The browser stand-in for plotly opens nothing, and `plotly.offline.plot` is replaced once and keeps its docstring.
  - The loader wrapper returns the module its loader creates.
  - → `test_chart_output_mutation.py`.
- **capacity.py**:
  - A 401 or 404 status with no error code, and an error code with no status, are each classified on their own; 402 and 405 are plain errors.
  - A capacity without a state is "unknown", with its text.
  - Without a time given, a reading is stamped with the current UTC time.
  - A long error message is cut to 300 characters.
  - The workspace name Fabric returns is used, and the configured name when Fabric gives none.
  - With no capacity list reader, an environment on a capacity reads as signed out.
  - A reading is partial only when an environment is neither read nor unassigned.
  - Freshness counts whole seconds, never below 0, and a reading is stale only when it is more than an hour old.
  - → `test_capacity_mutation.py`.
- **pdfreport.py**:
  - The regular and bold width tables match the Helvetica and Helvetica-Bold AFM files shipped with matplotlib, and oblique has the regular widths.
  - A width uses the table for characters 32 to 126, both included, and 556 for anything else.
  - Text that starts with emphasis has no empty run before it, and an escape gives its character.
  - Only neighbouring runs in the same font are joined.
  - A word exactly a line wide starts its own line unbroken; a longer word breaks into full lines; two words that fit exactly share a line, and one point less splits them.
  - The space after a word takes the font of its last letter.
  - A new page starts only when a line would go below the bottom margin and footer, and the new page starts at the top margin.
  - Pages are US Letter, and the first line sits at the left margin, one line height below the top margin.
  - Title, section heading, paragraph and blank line sit at their exact heights, in their fonts and sizes.
  - A paragraph wraps at the width between the margins.
  - A wrapped bullet has one bullet mark, and its text lines are indented.
  - A section heading moves to the next page unless three body lines fit under it.
  - The footer is right-aligned at the right margin, at the bottom margin less 18 points.
  - The trailer's size counts every cross-reference entry.
  - → `test_pdfreport_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `pdfreport.py` | 190 | `zip(..., strict=True)` to `strict=False` | Equivalent: three object numbers are zipped with the three fonts, so the lengths always match. |

# Mutation testing, batch 64

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The development roadmap and personal goals: tracks, milestones, suggestions and goals in the person's own words (`roadmap.py`).
- Environment profiles: which modules a notebook imports, whether each is optional, and where each stands on this PC and in Fabric (`envprofile.py`).
- The Direct Lake semantic model written as TMDL: table names, relationships, measures and columns (`semantic.py`).

The run was at `main` bcc8076fa (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/`, `examples/` or `app/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `roadmap.py` | test_profile_goals_impact (+ test_roadmap_mutation) | 68 of 68 (67 + 1) | 49 / 19 | **72.1%** | 48/67 → 67/67 | 1/1 → 1/1 | 68 / 0 | **100%** | none |
| `envprofile.py` | test_envprofile_edges, test_notebook_magics, test_prove_service_edges, test_receipts_env (+ test_envprofile_mutation) | 108 of 108 (108 + 0) | 98 / 10 | **90.7%** | 98/108 → 106/108 | none | 106 / 2 | **98.1%** | 2 equivalent |
| `semantic.py` | test_cloud, test_tmdl, test_model_date_table, test_model_dictionary, test_model_directlake, test_model_ossie (+ test_semantic_mutation) | 100 of 100 (99 + 1) | 89 / 11 | **89.0%** | 88/99 → 99/99 | 1/1 → 1/1 | 100 / 0 | **100%** | none |

One bug was found, in `envprofile.py`, and filed as #2773: a `%pip` line below one or more blank lines was reported on the first blank line rather than on its own line, because `\s*` at the start of the pattern also matches newlines. Only the line number shown was wrong. #2776 fixed it, and `test_a_pip_line_below_blank_lines_is_reported_on_its_own_line` now passes as a plain test. A run on the fixed module gives the same score: 106 of 108, with the same two equivalent mutants.

### What the survivors were, and the tests that now kill them

- **roadmap.py**:
  - The roadmap reports version 1 and up to 12 goals; goal text is kept to 300 characters.
  - The many-notebooks milestone needs five notebooks and the fixes milestone three fixes.
  - A goal in the person's own words gets a `g-` id with 12 hex characters.
  - An empty goal (none, empty or only whitespace) is refused, and the thirteenth goal is refused after twelve.
  - Marking an unknown goal is refused, and an own goal can be marked done and then not done.
  - Cleaning a stored goals list cuts the id to 80 characters and the dates to 40, keeps `done_at` only for own goals, and keeps at most 12 goals.
  - Learn links name only shipped lessons, with their titles (empty when a lesson has none), and a milestone needing an unshipped lesson is coming soon.
  - At most three suggestions, each the next step in a track already started, and never a step already chosen as a goal.
  - Lab and exercise needs are counted against the shipped labs and the exercises across shipped lessons.
  - → `test_roadmap_mutation.py`.
- **envprofile.py**:
  - A cell that isn't Python counts as parsed, with no imports.
  - A dynamic import after a blank first line is on line 2.
  - A bare `except`, or one that catches `ImportError`, `ModuleNotFoundError` or `Exception` (alone or in a tuple), makes the imports in its `try` optional; one that catches anything else doesn't.
  - A module imported at the top level and inside a function is required, whichever comes first; one imported only inside functions stays optional.
  - This PC isn't ready when a required module is missing even though every cell parsed, and is ready when only an optional module is missing.
  - A distribution with no version is left out of the runtime.
  - → `test_envprofile_mutation.py`.
- **semantic.py**:
  - A quoted name is unquoted only when it starts and ends with a quote.
  - Only an `scd1` or `scd2` entity with a surrogate key is a dimension.
  - A lookup joins the dimension's surrogate key; a lookup to a table with no surrogate key, without a column name, or to an unknown entity adds nothing.
  - A dimension never joins itself by its own key, and a key name of a table that isn't a dimension makes no relationship.
  - A column named like a dimension's key joins it, unless a lookup already joined from that column; a lookup from another column doesn't stop it.
  - Only a `date` column (in any case) gets the Short Date format.
  - `definition.pbism` is JSON indented two spaces.
  - → `test_semantic_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `envprofile.py` | 77 | `line = 0` to `line = 1` | Equivalent: the starting value is used only when the node imports nothing, and then there are no names to record it with. |
| `envprofile.py` | 109 | `return False` dropped (returns `None`) | Equivalent: the result is only used as a condition, where `None` and `False` act the same. |

# Mutation testing, batch 65

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The `IPython.display` stand-in a notebook cell imports when IPython isn't installed: display objects, image formats, the `display_*` helpers, and the import hook that provides it (`ipython_display.py`).
- The Direct Lake check over a semantic model's definition and its Delta tables' numbers: guardrails per capacity SKU, schema drift, calculated columns, relationship keys and the order of findings (`model_directlake.py`).
- The capacities file in a platform repository: reading `capacity.toml`, comparing it with what Fabric shows, and writing Fabric's state back (`git/capacity_files.py`).

The run was at `main` 81d17063d (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `model_directlake.py` has 172 mutants, so 150 were sampled; the other two modules were run in full.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/`, `examples/` or `app/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ipython_display.py` | test_ipython_display_edges, test_rich_outputs (+ test_ipython_display_mutation) | 87 of 87 (87 + 0) | 73 / 14 | **83.9%** | 73/87 → 86/87 | none | 86 / 1 | **98.9%** | 1 equivalent |
| `model_directlake.py` | test_model_directlake, test_model_directlake_service_edges (+ test_model_directlake_mutation) | 150 of 172 (147 + 3) | 99 / 51 | **66.0%** | 99/147 → 146/147 | 0/3 → 3/3 | 149 / 1 | **99.3%** | 1 equivalent |
| `git/capacity_files.py` | test_capacity_repo, test_capacity_files_edges, test_file_text_one_line, test_git_service_more_edges (+ test_capacity_files_mutation) | 91 of 91 (89 + 2) | 79 / 12 | **86.8%** | 78/89 → 89/89 | 1/2 → 2/2 | 91 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ipython_display.py**: the tests that call `install()` restore `sys.modules` and `sys.meta_path` with monkeypatch.
  - PNG bytes are read as PNG and shown as `image/png`; empty or unknown bytes are PNG too.
  - An object with both data and a URL, or with neither, isn't named by a URL.
  - JSON text given as data stays text, and `expanded` is off by default.
  - `display_json` wraps a value unless `raw=True`; with `raw=True` text is still wrapped; `display_png` gives an Image.
  - The stand-in reports IPython 8.30.0 as `version_info` and `__version__`.
  - Only `IPython` and `IPython.core` are packages; another name, or no stand-in yet, finds nothing.
  - `install()` leaves a real IPython module alone, and points a stand-in a cell already imported at the new kernel.
  - → `test_ipython_display_mutation.py`.
- **model_directlake.py**:
  - Every SKU's guardrails are pinned at Microsoft's numbers.
  - Row groups aren't estimated unless set, and files and rows are never shown as "about".
  - A partition that isn't an entity, or has no entity name, isn't read; a relationship needs both tables.
  - A table is named with its schema only when it has one, and a missing table is reported with `exists` False.
  - A calculated column fails on Direct Lake on SQL but not on OneLake, and the storage kind is reported.
  - A column with no source column is read by its name, and one with a source column by that.
  - Exactly at a limit, and at exactly 90% of it, is "near"; just under 90% says nothing.
  - A model exactly at its size limit is fine, the model's bytes are its tables' sum, and the size message gives whole GB.
  - Fails come before warnings whatever the table, and one duplicate value is singular.
  - → `test_model_directlake_mutation.py`.
- **git/capacity_files.py**:
  - Text is fine up to 256 characters; a name, id, field or workspace with other characters, or longer, is refused.
  - A missing file has no capacities and no problems.
  - A long section name is cut to 80 characters in a problem.
  - A capacity only the file has, or only Fabric has, is reported with every field exact.
  - Uncommitted changes need `confirm` by default, and the file isn't touched.
  - With no capacities, the file is the header as comments, and writing the same text again needs no confirm.
  - → `test_capacity_files_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `ipython_display.py` | 120 | `data[:8]` to `data[:9]` in the PNG check | Equivalent: a PNG that no longer matches falls through the JPEG and GIF checks to the default, which is also PNG. |
| `model_directlake.py` | 256 | `"info": 2` to `3` in the sort order | Equivalent: fail 0, warn 1 and info 3 sort the same as 0, 1, 2, and the counts use the keys, not the numbers. |

Neither involves text input, so leading or trailing whitespace and regex edge cases can't change them.

# Mutation testing, batch 66

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Comparing one table in two places, or now against an earlier Delta version: counts, keys on one side only, changed columns, totals and, for a table larger than memory, a checksum (`meta/compare.py`).
- Best practices for a layer's semantic model, checked on this PC from the metadata project (`model_practices.py`).
- The Learn lab checks: a learner's query against the answer, a table's shape, orphans, best-practice findings, speed and seeded defects (`learn/checks.py`).

The run was at `main` 3ca056487 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_learn_guides.py` and `test_practice_docs.py` reach `model_practices.py` but read `docs/`, so they were left out; that can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/compare.py` | test_meta_compare_edges, test_meta_compare_totals, test_dataops (+ test_meta_compare_mutation) | 124 of 124 (122 + 2) | 101 / 23 | **81.5%** | 100/122 → 118/122 | 1/2 → 2/2 | 120 / 4 | **96.8%** | 4 equivalent |
| `model_practices.py` | test_model_practices, test_learn_labs (+ test_model_practices_mutation) | 79 of 79 (78 + 1) | 58 / 21 | **73.4%** | 58/78 → 78/78 | 0/1 → 1/1 | 79 / 0 | **100%** | none |
| `learn/checks.py` | test_learn_labs, test_academy_dbsql_lab (+ test_learn_checks_mutation) | 114 of 114 (109 + 5) | 98 / 16 | **86.0%** | 97/109 → 109/109 | 1/5 → 5/5 | 114 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/compare.py**:
  - A missing table names the side it is missing on (either, the left or the right), with empty counts, rows and changes.
  - Columns only on one side are listed on that side, in a full compare and a hashed one.
  - A column that is a number on one side only gets no total.
  - Samples stop at 20 rows; the counts don't.
  - A keys-only table with the same keys on both sides has nothing changed.
  - A hashed compare starts with empty one-side and changed lists, and its integer totals stay exact integers.
  - Two integer columns hash as integers, so values that differ only past nine significant digits still differ; rounding applies only to an integer matched against a decimal.
  - A checksum is 16 hex digits.
  - Float totals equal within the tolerance (relative to the larger total on either side, or the absolute floor near zero) show no change, by size whatever the sign, and exactly at the bound still counts as the same.
  - → `test_meta_compare_mutation.py`.
- **model_practices.py**:
  - Each rule links to its docs section, and a rule can't be changed once made.
  - A column's model type comes from its contract first; without one, surrogate, key and looked-up columns are whole numbers, and Type 2 columns have their types only in a Type 2 entity.
  - A list of names names five, then counts the rest.
  - A described table with undescribed visible columns is found.
  - An odd table name is found on the table, and an odd column name on the column.
  - The model item takes each table's own columns and types.
  - → `test_model_practices_mutation.py`.
- **learn/checks.py**:
  - An answer is compared row by row up to a million rows; an answer longer than the limit is refused, one at the limit is compared.
  - Wrong columns are named, and the count of differing rows is how many answer rows are missing.
  - The detail says "in the same order" only when order was checked.
  - A defects query that doesn't run fails with why.
  - Speed is measured over three runs each by default, at least one even when none is asked for; exactly the goal passes; times are rounded to four places and the speedup to two; a run too quick to time counts as infinitely faster, with no speedup number.
  - → `test_learn_checks_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `meta/compare.py` | 27 | `if TYPE_CHECKING` negated | Equivalent: it only imports `pyarrow.dataset` at module load instead of for type checking; the annotations are strings, and the functions import it themselves. |
| `meta/compare.py` | 186 | `left or 0` to `left or 1` | Equivalent while `FLOAT_TOLERANCE` equals `FLOAT_FLOOR` (both 1e-9): with a zero or missing left total, the scale `max(1, abs(right))` times the tolerance is never below the floor the original takes anyway. |
| `meta/compare.py` | 186 | `right or 0` to `right or 1` | Equivalent for the same reason, with a zero or missing right total. |
| `meta/compare.py` | 219 | `strict=True` to `strict=False` | Equivalent: both sides' totals come from the same list of columns, so the lists always have the same length. |

# Mutation testing, batch 67

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The access cache: snapshot ids, the rows a refresh writes, and the per-workspace view OneLake > Access shows (`access_cache.py`).
- Opt-in usage data: counting, how long unsent days are kept, and what is sent (`telemetry.py`).
- The Snowflake function list's status on this PC and the page made from it (`snowflake_coverage.py`).

The run was at `main` 9911af348 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_snowflake_coverage.py` compares the committed `docs/snowflake-functions.md`, so it was left out for `snowflake_coverage.py`, which is why that module's "before" score is low; no other test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `access_cache.py` | test_access_cache, test_access_cache_service_edges (+ test_access_cache_mutation) | 76 of 76 (76 + 0) | 65 / 11 | **85.5%** | 65/76 → 76/76 | none | 76 / 0 | **100%** | none |
| `telemetry.py` | test_telemetry, test_corrupt_state_reads (+ test_telemetry_mutation) | 81 of 81 (78 + 3) | 59 / 22 | **72.8%** | 58/78 → 77/78 | 1/3 → 3/3 | 80 / 1 | **98.8%** | 1 equivalent |
| `snowflake_coverage.py` | test_snowflake_coverage_edges (+ test_snowflake_coverage_mutation) | 85 of 85 (84 + 1) | 22 / 63 | **25.9%** | 22/84 → 84/84 | 0/1 → 1/1 | 85 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **access_cache.py**:
  - No scans means no snapshots.
  - A snapshot id is the first 24 hex characters of the rows' SHA-256, and the same rows give the same id whatever the order of keys or rows.
  - A scan keeps the workspace and lakehouse names, or blanks when there are none.
  - A workspace seen only through one of its lakehouses is neither stale nor known.
  - The workspace scan's name wins over a lakehouse scan's, unless it is blank.
  - The summary counts people, groups, apps and guests exactly.
  - A lakehouse role's paths, Entra ids and item members are read back from JSON, and blanks read as empty lists.
  - → `test_access_cache_mutation.py`.
- **telemetry.py**:
  - Unsent days are kept for exactly 30 days: on 2026-10-17, 2026-09-17 is still sent and the day before it is dropped.
  - The last day sent is the latest one, nothing is sent while usage data is off, and today waits until it's over.
  - The build is the commit's first 12 characters, or "dev".
  - The install id is made once and returned, and Settings shows the one on file.
  - A file that can't be read reads as empty, and the file is key-sorted JSON with one-space indents.
  - The Windows version is sent on Windows, and the platform elsewhere.
  - An envelope is one custom event, version 2, with key-sorted string properties.
  - A report is posted once, with a 15-second limit and the caller's wait. A status of 400 or more keeps the days queued, and 399 doesn't.
  - → `test_telemetry_mutation.py`.
- **snowflake_coverage.py**:
  - The list is read from the JSON beside the module, the page is the repository's `docs/snowflake-functions.md`, and importing the module writes nothing.
  - A case name drops the "(what)" and upper-cases a function. `parse_json path` becomes `Path (v:a)` and operators are left as written.
  - An error is noted by its first line, cut to 160 characters, or by its type when it has no message.
  - A check that differs gives its own note, or the value it got. Checks that all run are counted ("1 value check", "2 value checks"), and their labels are named once each.
  - A function with checks takes its status from them, and one without is probed.
  - A sample runs without the two small tables unless asked. One DuckDB can't run fails with its first line, and one sqlglot can't read is unread. A refused one gives the reason without the statement prefix, and a labelled one differs with every label.
  - A listed sample runs once, on the two tables. A probe tries 0 to 4 NULLs, puts a window function inside `OVER`, stops at the first arity that runs, and prefers a refusal to DuckDB's errors.
  - The sample is shown only for a function without checks.
  - The page counts each status and gives each group a table with a Sample column only when the group has a sample. Pipes are escaped and lines joined.
  - → `test_snowflake_coverage_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `telemetry.py` | 95 | `destination()[0]` to `destination()[1]` in `enabled()` | Equivalent: `destination()` returns a connection string and whose it is together, or two empty strings, so both parts are empty or both are set. |

It doesn't involve text input, so leading or trailing whitespace and regex edge cases can't change it.

# Mutation testing, batch 68

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Which Databricks notebook helpers (`dbutils` calls and `%` magics) run on this PC, and the page that lists them (`databricks_notebook_helpers.py`).
- A notebook's version history as the app calls it: list, open, compare, checkpoint, restore and delete (`notebook_versions_service.py`).
- Saved and shared queries for the Warehouse page (`sql_queries_service.py`).

The run was at `main` 9091dd082 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. The only test file that reaches `databricks_notebook_helpers.py`, `test_databricks_notebook_helpers.py`, compares the committed `docs/databricks-notebook-helpers.md`, so it was left out. Its "before" run used a test file that only imports the module, so every mutant survived it; the new tests don't read `docs/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_notebook_helpers.py` | an import-only file (+ test_databricks_notebook_helpers_mutation) | 44 of 44 (44 + 0) | 0 / 44 | **0%** | 0/44 → 44/44 | none | 44 / 0 | **100%** | none |
| `notebook_versions_service.py` | test_notebook_versions_service_edges, test_notebook_versions, test_notebook_folders (+ test_notebook_versions_service_mutation) | 28 of 28 (28 + 0) | 24 / 4 | **85.7%** | 24/28 → 28/28 | none | 28 / 0 | **100%** | none |
| `sql_queries_service.py` | test_sql_queries, test_sql_queries_edges (+ test_sql_queries_mutation) | 76 of 76 (70 + 6) | 62 / 14 | **81.6%** | 62/70 → 70/70 | 0/6 → 6/6 | 76 / 0 | **100%** | none |

One `sql_queries_service.py` mutant (`i += 1` to `i += 0` in the header loop) never ends, so it is stopped at the timeout and counts as killed, before and after.

No bugs were found.

### What the survivors were, and the tests that now kill them

- **databricks_notebook_helpers.py**:
  - The stand-in notebookutils raises on every call, so a probe never does real work.
  - The walk over `dbutils` goes into namespaces and widgets, skips private names and `help`, and leaves out `bind` and `describe` only on widgets.
  - A probe finds a missing part at any depth, reports a refusal with its message, counts any other error as running, and prints nothing.
  - Each call, cell magic and line magic gets its status and note; Scala and R cells are refused.
  - The page counts each status per group, lists every helper with its prefix, turns `|` in a note into `/`, and ends with the source.
  - The list file is read from beside the module, and importing the module writes nothing.
  - → `test_databricks_notebook_helpers_mutation.py`.
- **notebook_versions_service.py**:
  - A version and a compare read back their label, or an empty one.
  - Unsaved cells keep their compute, and none means the default.
  - A delete names the version it deleted.
  - → `test_notebook_versions_service_mutation.py`.
- **sql_queries_service.py**:
  - A file of only header lines has no SQL; one line of spaces after the header is the gap and is dropped, a second one stays.
  - A good query has an empty problem; a name, warehouse or description exactly at its limit is taken.
  - The fingerprint is the first 16 hex digits of SHA-256.
  - A query exactly at the size limit is saved and one byte more is refused, and a listed file exactly at the limit is read while a larger one says it is larger than 64 KB.
  - → `test_sql_queries_mutation.py`.

### Left

None.

# Mutation testing, batch 69

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- OneLake > Access across every workspace as the app calls it: read the cache, refresh it, and choose who may read it (`access_cache_service.py`).
- The notebook explorer's calls: list, add, rename, delete and Show in File Explorer (`notebook_resources_service.py`).
- The data dictionary card on the Semantic model tab: read, export, save, preview and import (`model_dictionary_service.py`).

The run was at `main` 996b6ee54 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_support_bundle_save.py` reaches `notebook_resources_service.py` but reads `app/`, and `test_state_writes.py` reaches `model_dictionary_service.py` but reads `docs/`, so both were left out, which can only lower the "before" scores.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `access_cache_service.py` | test_access_cache, test_access_cache_service_edges (+ test_access_cache_service_mutation) | 54 of 54 (54 + 0) | 48 / 6 | **88.9%** | 48/54 → 54/54 | none | 54 / 0 | **100%** | none |
| `notebook_resources_service.py` | test_notebook_resources, test_notebook_resources_calls (+ test_notebook_resources_service_mutation) | 25 of 25 (25 + 0) | 22 / 3 | **88.0%** | 22/25 → 25/25 | none | 25 / 0 | **100%** | none |
| `model_dictionary_service.py` | test_model_dictionary, test_model_dictionary_mutation, test_model_dictionary_service_edges (+ test_model_dictionary_service_mutation) | 22 of 22 (22 + 0) | 21 / 1 | **95.5%** | 21/22 → 22/22 | none | 22 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **access_cache_service.py**:
  - The cache names each workspace and lakehouse as Fabric does, and a lakehouse with no name is kept with an empty one.
  - A connection that doesn't say otherwise reads the lakehouse with schemas.
  - Before and after the tables are set up, the cache says this PC may write only when its connection is for writing.
  - Choosing no one, or only blanks, is refused even when confirming, and nothing is written.
  - → `test_access_cache_service_mutation.py`.
- **notebook_resources_service.py**:
  - Showing the folder a second time shows the same folder.
  - The program that shows a file starts with no flags where there is no no-window flag, and with it where there is one.
  - Handles are closed for the program except on Windows.
  - → `test_notebook_resources_service_mutation.py`.
- **model_dictionary_service.py**:
  - When a save fails part way, a column is named with its table, and a table on its own.
  - → `test_model_dictionary_service_mutation.py`.

### Left

None.

# Mutation testing, batch 70

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The capacity settings history's app call: read the capacities the signed-in person can see, and their overrides for an administrator (`capacity_history_service.py`).
- The tenant settings history's app calls: read Fabric's tenant settings, and show what changed (`tenant_history_service.py`).
- Measured usage files as the app calls them: preview, import, list, remove and reconcile with this app's Fabric runs (`usage_service.py`).

The run was at `main` 8403031ca (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_opportunities.py` and `test_rpc_surface.py` also reach `usage_service.py`, but they read `docs/` or `app/`, so they were left out, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `capacity_history_service.py` | test_capacity_history (+ test_tenant_history_service_mutation) | 20 of 20 (20 + 0) | 19 / 1 | **95.0%** | 19/20 → 20/20 | none | 20 / 0 | **100%** | none |
| `tenant_history_service.py` | test_tenant_history, test_capacity_history (+ test_tenant_history_service_mutation) | 37 of 37 (36 + 1) | 27 / 10 | **73.0%** | 27/36 → 36/36 | 0/1 → 1/1 | 37 / 0 | **100%** | none |
| `usage_service.py` | test_usage_import, test_opportunities_share, test_estate_service_edges, test_risk_service_edges, test_platform_setup_steps, test_opportunities_assess_mutation (+ test_usage_service_mutation) | 31 of 31 (31 + 0) | 19 / 12 | **61.3%** | 19/31 → 31/31 | none | 31 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **capacity_history_service.py**:
  - A 401 is recorded as unreadable, like a 403, for the capacities and for an administrator's overrides; a 404 is a failed read.
  - → `test_tenant_history_service_mutation.py`.
- **tenant_history_service.py**:
  - A 401 or 403 is unreadable whether it comes as the status or only in the message; another status, or a number that only starts with 403, is a failed read.
  - Without a known tenant, a read asks the person to sign in again.
  - The history shows 200 changes by default, newest first, and says it is connected.
  - The lakehouse is read with schemas unless its connection says otherwise.
  - The last read is the newest successful one; a failure before it, or at the same moment, isn't a gap.
  - → `test_tenant_history_service_mutation.py`.
- **usage_service.py**:
  - A file name is cut to 200 characters, and a missing one reads as `usage.csv`.
  - A run names its workspace only with both an id and a name; without a metadata project the runs' names are kept, and a project's environment adds a name only when it has both, never over a run's own.
  - Only Fabric runs with a run id give estimates.
  - Capacity names map to their environments case-blind, and an environment without a capacity name is skipped.
  - The reconciliation reads 30 days by default, from up to 500 runs already on this PC.
  - → `test_usage_service_mutation.py`.

### Left

None.

# Mutation testing, batch 71

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Dataflow Gen2 from a Fabric workspace: the list, the queries and the refresh history (`workspace_dataflows_service.py`).
- Where Windows keeps the Downloads and Documents folders (`known_folders.py`).
- The record `agent run` leaves when it stops before or as it starts (`agent_start.py`).

The run was at `main` 570e1faf6 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_agent_runbook.py` reaches `agent_start.py` but reads `docs/`, so it was left out, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workspace_dataflows_service.py` | test_workspace_dataflows (+ test_workspace_dataflows_mutation) | 13 of 13 (13 + 0) | 12 / 1 | **92.3%** | 12/13 → 13/13 | none | 13 / 0 | **100%** | none |
| `known_folders.py` | test_windows_folders_and_app_control_edges, test_notebook_formats_service_edges, test_prove_service_mutation (+ test_known_folders_mutation) | 24 of 24 (24 + 0) | 21 / 3 | **87.5%** | 21/24 → 24/24 | none | 24 / 0 | **100%** | none |
| `agent_start.py` | test_agent, test_agent_clock_and_folder (+ test_agent_start_mutation) | 26 of 26 (25 + 1) | 18 / 8 | **69.2%** | 18/25 → 25/25 | 0/1 → 1/1 | 26 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workspace_dataflows_service.py**:
  - The newest 50 refreshes are listed: 49 and 50 are listed whole, and 51 or 52 are cut to the first 50.
  - → `test_workspace_dataflows_mutation.py`.
- **known_folders.py**:
  - Windows is asked only where a folder is, never to create or check it, with no token.
  - Windows is asked for the whole folder id, all 16 bytes of it.
  - An answer Windows marks as failed is no answer, even when a path came back, and so is an empty one.
  - → `test_known_folders_mutation.py`.
- **agent_start.py**:
  - The record is kept under 256 KB.
  - A reason given as text keeps 2000 characters; a longer one is cut to 1999 and an ellipsis.
  - Only a crash lists its frames, a crash given as text lists none, and a deep crash lists its last 12.
  - A new entry that brings the file exactly to the limit is added with nothing dropped; one byte more drops the older half.
  - The trim keeps the newer half from the first whole line after its middle.
  - → `test_agent_start_mutation.py`.

### Left

None.

# Mutation testing, batch 72

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The run logs on this PC that grow one line at a time and trim themselves (`runlog.py`).
- Copy jobs from a Fabric workspace: the list, the definition and the runs (`workspace_copy_jobs_service.py`).
- Who can read what: the workspace access review, OneLake data access roles and who can read a table (`access.py`).

The run was at `main` 4c0ab9a0e (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `timeutil.py` and `workspace_pipelines_service.py` were also run and already scored 100% (16/16 and 12/12), so they are not in the table.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `runlog.py` | test_local_retention_gaps, test_local_schedules_mutation (+ test_runlog_mutation) | 23 of 23 (21 + 2) | 7 / 16 | **30.4%** | 7/21 → 21/21 | 0/2 → 2/2 | 23 / 0 | **100%** | none |
| `workspace_copy_jobs_service.py` | test_workspace_copy_jobs (+ test_workspace_copy_jobs_mutation) | 9 of 9 (9 + 0) | 8 / 1 | **88.9%** | 8/9 → 9/9 | none | 9 / 0 | **100%** | none |
| `access.py` | test_access_cache, test_access_cache_service_edges, test_access_cache_service_mutation, test_estate, test_estate_service_edges, test_fail_closed, test_shortcut_center, test_shortcut_fixes (+ test_access_mutation) | 84 of 84 (84 + 0) | 75 / 9 | **89.3%** | 75/84 → 84/84 | none | 84 / 0 | **100%** | none |

One bug was found, in `access.py`, and filed as #2819: a data access role name ending in a line break passes the name check, though a role name is letters and digits only. A strict xfail pins it: `test_a_role_name_ending_in_a_line_break_is_refused`. Whoever fixes #2819 drops that xfail in the same PR.

### What the survivors were, and the tests that now kill them

- **runlog.py**:
  - The default sizes are 8 MB and 10,000 rows.
  - A log in folders that don't exist yet is created with them.
  - A file exactly at the trim size is left alone; one row over it keeps half of it by default, the newest rows.
  - Rows that add up to exactly the size kept are all kept, and one byte less keeps one row fewer.
  - The row limit wins when it is smaller, and the newest row is kept even when it alone is over the size kept.
  - → `test_runlog_mutation.py`.
- **workspace_copy_jobs_service.py**:
  - The newest 50 runs are listed: 49 and 50 are listed whole, and 51 or more are cut to the first 50.
  - → `test_workspace_copy_jobs_mutation.py`.
- **access.py**:
  - A person is named by display name, then email, then id, and a guest account is recognised in any letter case.
  - Which review notes each person gets, in Prod and Dev, and that people with notes come first, then users, then by name.
  - A role's paths come only from its Permit rules about paths.
  - How a read role is built, which names, paths and member lists are refused, and that an edited role keeps its item members.
  - Which role paths reach a table: a path that only starts the same way doesn't.
  - Which answers mean OneLake security is off, and who can read a table and why.
  - → `test_access_mutation.py`.

### Left

None.

# Mutation testing, batch 73

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The decision patterns reader, which turns the decision patterns page into what the app ships (`learn/patterns.py`).
- Which of a notebook's cells a run runs, and waiting for each cell (`notebook_cells.py`).
- The Workspaces page previews: where a previewed file goes on this PC and which files are refused (`preview_service.py`).

The run was at `main` 21f72ad25 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. The test file that checks `learn/patterns.py`, `test_decision_patterns.py`, reads `docs/`, so it was left out; the "before" run for that module used `test_notebook_cells.py`, which doesn't reach it, so its "before" score is 0. The new tests use small pages of their own and don't read `docs/`. `test_state_writes.py`, which lists the module's file write, also reads `docs/` and was left out too.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `learn/patterns.py` | test_notebook_cells (+ test_learn_patterns_mutation) | 21 of 21 (21 + 0) | 0 / 21 | **0%** | 0/21 → 21/21 | none | 21 / 0 | **100%** | none |
| `notebook_cells.py` | test_notebook_cells, test_integrations_executions_mutation, test_pipeline_notebooks_edges, test_replay_edges, test_meaningful_diffs (+ test_notebook_cells_mutation) | 32 of 32 (32 + 0) | 29 / 3 | **90.6%** | 29/32 → 32/32 | none | 32 / 0 | **100%** | none |
| `preview_service.py` | test_preview, test_audit (+ test_preview_service_mutation) | 26 of 26 (25 + 1) | 18 / 8 | **69.2%** | 18/25 → 25/25 | 0/1 → 1/1 | 26 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **learn/patterns.py**:
  - Each `### DP-nn` section gives its title, body, language and example, and a section ends at the next `##` heading or at the end of the page.
  - Only an id of two digits on a line of its own starts a pattern.
  - A section with no bold title line uses its id as the title, and one with no example has an empty language and example. A bold line that isn't first, or has words after it, isn't the title.
  - Only the first fenced block is the example, and a fence with no language keeps its example.
  - A list item wrapped with two spaces joins its line; a deeper indent doesn't. One blank line stays, and two or more become one.
  - `build` reads the page from the folder it's given and sorts by id, `load` reads the shipped file next to the module, and run as a script it writes the patterns next to itself, sorted, with a one-space indent and without escaping non-ASCII text.
  - → `test_learn_patterns_mutation.py`.
- **notebook_cells.py**:
  - The first poll asks from version -1, and a snapshot with no version keeps the last one asked.
  - A cell that finished or was stopped with no outputs gives an empty list, a cell stopped at once keeps the outputs it started with, and a last snapshot with no outputs gives the ones seen before it.
  - → `test_notebook_cells_mutation.py`.
- **preview_service.py**:
  - A previewed file keeps its extension only when it is one to ten letters or digits.
  - A destination outside the preview's own folder is refused.
  - A file exactly at the size limit is read, and one byte over isn't.
  - A path outside `Files/`, or with a `..` step, is refused before anything is downloaded; dots that are part of a name are allowed.
  - Each preview gets a folder of its own, removed when the preview is read, when reading fails, and when the download stops at the limit. A download that fails before its folder exists still says why.
  - A table preview attaches the lakehouse before reading the table, and a notebook preview asks for the notebook's `ipynb` definition.
  - → `test_preview_service_mutation.py`.

### Left

None.

# Mutation testing, batch 74

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What Windows' application control refuses to load, and the line an error gets when it does (`app_control.py`).
- The Warehouse page's Monitoring tab: finding the monitoring database, a view, and building the monitoring querysets (`warehouse_monitoring_service.py`).
- Set up for VS Code: the files it plans and writes into a repository (`vscode_setup.py`).

The run was at `main` 238a53d35 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_vscode_setup.py` also reaches `vscode_setup.py` but reads `docs/`, so it was left out, which can only lower that module's "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `app_control.py` | test_app_control, test_app_control_policy, test_windows_folders_and_app_control_edges (+ test_app_control_mutation) | 73 of 73 (70 + 3) | 65 / 8 | **89.0%** | 63/70 → 70/70 | 2/3 → 3/3 | 73 / 0 | **100%** | none |
| `warehouse_monitoring_service.py` | test_warehouse_monitoring, test_warehouse_monitoring_build_again (+ test_warehouse_monitoring_service_mutation) | 64 of 64 (63 + 1) | 49 / 15 | **76.6%** | 49/63 → 63/63 | 0/1 → 1/1 | 64 / 0 | **100%** | none |
| `vscode_setup.py` | test_vscode_setup_edges (+ test_vscode_setup_mutation) | 33 of 33 (33 + 0) | 12 / 21 | **36.4%** | 12/33 → 32/33 | none | 32 / 1 | **97.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **app_control.py**:
  - The file is mapped with the flag that skips its dependencies (1), and the kernel32 handle keeps the last error.
  - The scan checks at most 5,000 binaries.
  - The 20th error in a chain is read and the 21st is not, and an error raised while handling another is followed to the reason.
  - An import names the package without its leading underscore.
  - An engine inside the folder the engine_folder policy names is not the person's own.
  - → `test_app_control_mutation.py`.
- **warehouse_monitoring_service.py**:
  - A queryset is the app's only when its description starts and ends as the app's does.
  - At most 10 databases are tried, names ending in `_Database` first, then by name.
  - The first probe row gives when and how many; no rows is monitored with nothing seen.
  - The views are listed once monitoring is found, not before, and at most three problems are reported.
  - A view with nothing recorded yet is empty and not cut short; one that fails for another reason says so.
  - 100 warehouses are built and 101 refused; a created queryset keeps the id Fabric gave it, or an empty one when Fabric gives none.
  - A remembered workspace is added to the ones already there.
  - → `test_warehouse_monitoring_service_mutation.py`.
- **vscode_setup.py**:
  - JSON files are indented two spaces and end with a line break, and each file has its text.
  - The problem matcher reads each group of a finding, and the Problems panel is cleared.
  - A plan tells new, the same and already there apart, a folder where a file would go included, and counts the new ones.
  - Only a list of the listed files is accepted.
  - Files are written into folders that don't exist yet or already do.
  - → `test_vscode_setup_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `vscode_setup.py` | 125 | `state == "exists" and path.is_file()` to `or` | Equivalent: a path that isn't there isn't a file, so `or` gives the same answer for a new file; for a folder that is there, reading it fails, the error is caught and the state stays "exists", as it does when the read is skipped. |

# Mutation testing, batch 75

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Sorting, filtering and column statistics on a SQL result as the grid shows it (`sql_view.py`).
- Notebook export to Downloads, import from a picked file, the page to print and the read-only report (`notebook_formats_service.py`).
- The semantic model best practices check: its notebook, run parameters, saved errors and findings (`model_bpa.py`).

The run was at `main` 8b8919a18 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_sql_exec.py` reads `app/`, so it was left out of the `sql_view.py` set; no other test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_view.py` | test_sql_view, test_regex_anchors (+ test_sql_view_mutation) | 115 of 115 (107 + 8) | 106 / 9 | **92.2%** | 101/107 → 107/107 | 5/8 → 8/8 | 115 / 0 | **100%** | none |
| `notebook_formats_service.py` | test_notebook_formats_service_edges, test_notebook_report, test_notebook_formats, test_chart_libraries (+ test_notebook_formats_service_mutation) | 124 of 124 (121 + 3) | 100 / 24 | **80.6%** | 100/121 → 120/121 | 0/3 → 3/3 | 123 / 1 | **99.2%** | 1 equivalent |
| `model_bpa.py` | test_model_bpa, test_fips, test_not_set_up (+ test_model_bpa_mutation) | 36 of 36 (36 + 0) | 27 / 9 | **75.0%** | 27/36 → 36/36 | none | 36 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **sql_view.py**:
  - At most three sort keys are read, and a fourth is ignored even when it names a column the result lacks.
  - A duration column is filtered as its text, not refused as a date.
  - A filter of exactly 200 characters is read, and 201 is refused.
  - A filter or sort on the position just past the last column is refused as a column the result doesn't have, even when the filter box is empty.
  - The distinct count is never above the rows that have a value, and a missing count reads as 0.
  - → `test_sql_view_mutation.py`.
- **notebook_formats_service.py**:
  - A report is at most 64 MB, and a page exactly at the limit is kept while one byte more is refused, keeping the report from before.
  - An archive exactly at its size limit is read, and one character more is refused.
  - A free notebook name is looked for up to number 9999, both when checking and when importing; a free file name in Downloads up to number 999, for an export and for a report.
  - An imported notebook is written as ipynb JSON indented by one space.
  - Notes are kept under the name of the notebook they belong to.
  - A failed write whose file is already gone keeps the write's own error.
  - The page to print and the report need a list of cells: a tuple, or a list holding text, is refused.
  - The run line is at most 200 characters.
  - → `test_notebook_formats_service_mutation.py`.
- **model_bpa.py**:
  - The check notebook is nbformat 4.5.
  - The notebook hash doesn't depend on key order.
  - A saved error's type is capped at 200 characters.
  - A finding with empty, missing or other empty-valued fields reads them as empty text, and filled fields keep their text.
  - → `test_model_bpa_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `notebook_formats_service.py` | 83 | `strict=True` to `strict=False` in `notebook_formats_import_check` | Equivalent: the names are made one for each notebook of the same list, so both sides always have the same length. |

# Mutation testing, batch 76

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Query parameters on the SQL page: finding `:name` and `{{name}}` outside strings, comments and quoted names, and reading each value as its type (`sql_params.py`).
- Opening and saving `.sql` files on this PC from the SQL page (`sql_files.py`).
- Reading base64 strictly, the same on every Python 3.12 patch release (`b64.py`).

The run was at `main` e67fa8480 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run. Two `sql_params.py` mutants loop for ever and are stopped at the time limit; as in earlier batches, they count as killed, before and after.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_shell_commands.py` also reaches `sql_files.py` but reads `docs/` and `app/`, so it was left out, which can only lower that module's "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_params.py` | test_sql_params (+ test_sql_params_mutation) | 122 of 122 (113 + 9) | 83 / 39 | **68.0%** | 82/113 → 111/113 | 1/9 → 6/9 | 117 / 5 | **95.9%** | 5 equivalent |
| `sql_files.py` | test_sql_files (+ test_sql_files_mutation) | 75 of 75 (73 + 2) | 55 / 20 | **73.3%** | 55/73 → 72/73 | 0/2 → 2/2 | 74 / 1 | **98.7%** | 1 equivalent |
| `b64.py` | test_b64 (+ test_b64_mutation) | 17 of 17 (16 + 1) | 14 / 3 | **82.4%** | 13/16 → 15/16 | 1/1 → 1/1 | 16 / 1 | **94.1%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **sql_params.py**:
  - At most 50 parameters of at most 4,000 characters each: 50 and 4,000 are taken, 51 and 4,001 refused.
  - Each malformed parameter list is refused by name: not a list, an entry that isn't `{name, type, value}`, a name that isn't text or isn't letters, digits and `_`, a value that isn't text.
  - Finding names and writing `{{name}}` as `:name` read Spark SQL by default, where `[...]` isn't a quoted name; a positional query reads T-SQL by default, where it is.
  - Strings and comments end exactly where they end: a string at the very start, `/*/` opening a comment, the shortest comment, a parameter just before or just after a comment ends, a string that never ends, `''` and `\'` inside a string, a doubled quote right before the closing one, a string closing on the last character, and a parameter right after a string.
  - A backslash is an ordinary character in a T-SQL string.
  - → `test_sql_params_mutation.py`.
- **sql_files.py**:
  - The limit is 5 MB: a file or a save of exactly 5 MB is taken, one byte more is refused, and the refusal names 5 MB.
  - A folder named `.sql` isn't a file.
  - A save with text that isn't text, another encoding or another line ending is refused and writes nothing.
  - A Windows reparse point (a junction, a mount point) is refused as a link; a stat result without file attributes, or a Python without the reparse-point constant, reads nothing as one.
  - → `test_sql_files_mutation.py`.
- **b64.py**:
  - A character outside the alphabet is refused even where skipping it would decode.
  - Padding followed by data is refused without the standard library's help, and no padding at all is fine.
  - → `test_b64_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sql_params.py` | 34 | `j < 0` to `j < 1`, and to `j <= 0` | Equivalent: `j` is where the line break after a `--` is found, searching from the `--` itself, so it is at least 2 when found and -1 when not; it is never 0. |
| `sql_params.py` | 37 | `j < 0` to `j < 1`, and to `j <= 0` | Equivalent: the same for the `*/` after a `/*`, searched from 2 past it. |
| `sql_params.py` | 65 | `a <= at` to `a < at` | Equivalent: a span starts at a quote, `-`, `/` or `[`, and a parameter starts at `:` or `{`, so a parameter never starts where a span does. |
| `sql_files.py` | 50 | `return False` dropped (returns `None`) | Equivalent: the result is only used as a condition, where `None` and `False` act the same. |
| `b64.py` | 20 | `pad < len(text) - 2` to `- 3` | Equivalent: the two differ only when `=` is the third character from the end, and then what follows it is three characters, never `=` or `==`, so the next test refuses it either way. |

# Mutation testing, batch 77

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Power BI reports, paginated reports and dashboards shown in the app: their embed addresses, the sign-in token's expiry and what each kind can do (`powerbi_embed.py`).
- Git repository addresses: which are accepted, the folder a clone gets, and the links to the repository, branch, commit and pull requests (`git/remote.py`).
- Notebook environments and packages: listing, attaching, installing and removing, and the organization settings that allow or stop a package install (`environment_service.py`).

The run was at `main` d177fe5b4 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `git/remote.py` has 185 mutants, so 150 were sampled; the other two have fewer than 150, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_rpc_surface.py` reads `app/`, so it was left out of the `environment_service.py` set; no other test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `powerbi_embed.py` | test_powerbi_embed, test_powerbi_embed_service_edges, test_credential_expiry (+ test_powerbi_embed_mutation) | 84 of 84 (84 + 0) | 58 / 26 | **69.0%** | 58/84 → 84/84 | none | 84 / 0 | **100%** | none |
| `git/remote.py` | test_git_remote_links, test_git (+ test_git_remote_mutation) | 150 of 185 (143 + 7) | 129 / 21 | **86.0%** | 124/143 → 143/143 | 5/7 → 7/7 | 150 / 0 | **100%** | none |
| `environment_service.py` | test_environments, test_fabric_environment, test_shared_sessions (+ test_environment_service_mutation) | 58 of 58 (58 + 0) | 42 / 16 | **72.4%** | 42/58 → 58/58 | none | 58 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **powerbi_embed.py**:
  - Each kind (report, paginated report, dashboard) has exactly its own list of what it can do, the events it sends and its limits.
  - An error from Power BI other than 401, 403 or 404 comes through with its own status.
  - A token already past its expiry has 0 seconds left, not a negative count.
  - The expiry is read from a token whatever padding its claims need.
  - A report or dashboard keeps the id, name and model Power BI gives, and falls back to the asked id and empty text when they are missing; so do a dashboard's tiles.
  - An item with no type is called "this item" in the message.
  - → `test_powerbi_embed_mutation.py`.
- **git/remote.py**:
  - An empty or blank address asks for one, an http address is refused with the reason, and a file address is kept as given.
  - A sign-in name in the address of 30 characters or more, or starting like a GitHub token, is refused; 29 characters is accepted.
  - A clone folder is named from the last part of an scp, https or Windows address.
  - An address with no server has no host.
  - GitHub, Bitbucket and GitLab addresses name their owner and repository, including GitLab's nested groups.
  - An Azure DevOps address needs both a project and a repository, on either host name.
  - Links with no branch have no branch or pull request links, and Azure DevOps links name the branch, commit and target branch.
  - An address with only a password shows without it.
  - → `test_git_remote_mutation.py`.
- **environment_service.py**:
  - Each organization setting that stops a package install (packages turned off, a package folder setting that can't be used, no PyPI and no folder) stops it with its own message, before anything starts.
  - The package source is named for each combination of folder and PyPI settings, and an unset folder is no folder.
  - Packages are allowed unless the setting is false.
  - Removing packages starts a removal job, and a job is read as given.
  - A new environment made for a notebook has a name of at most 50 characters.
  - Attaching an environment looks up the notebook's session without making one.
  - Only a process kernel for a named notebook gets the notebook's packages.
  - → `test_environment_service_mutation.py`.

# Mutation testing, batch 78

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. None of the three has a `<`, `<=`, `>` or `>=` comparison, so there are no boundary mutants this time.
- Moving surrogate keys from LH_Keys into the platform's keys database, keeping or renumbering them, and checking the copy (`central/migrate.py`).
- The demonstration fixture, turned into what Fabric's definition and job instance APIs return (`opportunities/fixture.py`).
- Trust badges: reading endorsement with Fabric's admin tenant scan, and a product's semantic models and reports (`central/trust_service.py`).

The run was at `main` 5279c61dc (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Every run in this batch, before and after, was made with `PYTHONDONTWRITEBYTECODE=1`. Without it, a mutant could be imported from the previous mutant's cached bytecode when the two have the same size and are written in the same second, so it took that mutant's result ([#2836](https://github.com/kratosdatagod/fabric-desktop/issues/2836)). With it, two runs gave the same result for every mutant.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_opportunities.py` also reaches `fixture.py` but reads `docs/`, so it was left out, which can only lower that module's "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/migrate.py` | test_central_migrate_edges (+ test_central_migrate_mutation) | 21 of 21 (21 + 0) | 17 / 4 | **81.0%** | 17/21 → 21/21 | none | 21 / 0 | **100%** | none |
| `opportunities/fixture.py` | test_opportunities_share (+ test_opportunities_fixture_mutation) | 16 of 16 (16 + 0) | 8 / 8 | **50.0%** | 8/16 → 16/16 | none | 16 / 0 | **100%** | none |
| `central/trust_service.py` | test_trust_badges, test_trust_service_edges (+ test_trust_service_mutation) | 53 of 53 (53 + 0) | 48 / 5 | **90.6%** | 48/53 → 53/53 | none | 53 / 0 | **100%** | none |

No bugs were found in these modules. One was found in the mutation tool itself (#2836, above).

### What the survivors were, and the tests that now kill them

- **central/migrate.py**:
  - Keys go to the platform in calls of 5,000, in order, with none left out: 12,001 keys go as 5,000, 5,000 and 2,001, and exactly 5,000 go in one call.
  - An answer without a count counts nothing as copied.
  - At most ten differing keys are listed, though every one of them counts against the match.
  - Renumbered counts add up across tables, and a table without one adds nothing.
  - → `test_central_migrate_mutation.py`.
- **opportunities/fixture.py**:
  - A notebook becomes an nbformat 4.5 `.ipynb` part, cell by cell, with its default lakehouse.
  - A pipeline becomes one inline part.
  - Daily runs go back one day at a time from the day before, each at 06:00:00 whatever the time of day the fixture is dated, and each ends its own number of seconds after it starts; one daily run is one run, and no schedule is none.
  - The loaded demonstration has every scheduled run of every item, and only the price fields it uses.
  - → `test_opportunities_fixture_mutation.py`.
- **central/trust_service.py**:
  - A published product's semantic models and reports are scanned with their own workspaces and reported by name.
  - A draft product's semantic models and reports aren't scanned.
  - A group entry for an item already behind one of the product's ports keeps the port's details.
  - A group entry without a name is reported by its id, and fields it leaves out are recorded as blank.
  - → `test_trust_service_mutation.py`.

### Left

Nothing is left: every mutant in the three modules is killed.

# Mutation testing, batch 79

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Your own SQL templates on the Warehouse page: reading and writing a template file, the places a template is kept, and saving, importing, exporting and deleting one (`sql_templates_service.py`).
- Comments on notebook cells: adding, replying, editing, deleting and resolving, line ranges, and who wrote a comment (`comments_service.py`).
- Metadata runs on Fabric capacity: the bundle the app uploads, the runner notebook, its run parameters, and the run inside Fabric (`fabric_compute.py`).

The run was at `main` 1b1e47552 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run. One `sql_templates_service.py` mutant and two `comments_service.py` mutants loop for ever and are stopped at the time limit; as in earlier batches, they count as killed, before and after.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_templates_service.py` | test_sql_templates, test_sql_templates_edges, test_sql_queries_edges, test_sql_queries_mutation, test_file_replace (+ test_sql_templates_mutation) | 140 of 140 (132 + 8) | 112 / 28 | **80.0%** | 112/132 → 132/132 | 0/8 → 8/8 | 140 / 0 | **100%** | none |
| `comments_service.py` | test_notebook_comments, test_notebook_comments_edges, test_regex_anchors (+ test_notebook_comments_mutation) | 138 of 138 (131 + 7) | 121 / 17 | **87.7%** | 118/131 → 130/131 | 3/7 → 6/7 | 136 / 2 | **98.6%** | 2 equivalent |
| `fabric_compute.py` | test_fabric_compute, test_fabric_compute_edges, test_landing_zones, test_fips (+ test_fabric_compute_mutation) | 59 of 59 (59 + 0) | 49 / 10 | **83.1%** | 49/59 → 59/59 | none | 59 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **sql_templates_service.py**:
  - A title of 100 characters, a group or description of 200, and a template file of exactly 64 KB are taken; one more is refused, with the limit in the message.
  - A place with exactly 200 templates lists them all with no note; with 201 the note says so.
  - A file that is only header lines has no SQL, and a line of spaces right after the header is dropped.
  - A template saved without saying so isn't an endpoint template, and a file that can't be read is never listed as one.
  - Editing a template returns the file it wrote.
  - An import that isn't text, or is over 64 KB, is refused with its own message.
  - A repository place is labelled by its name, or by its folder when it has none, and one that no longer exists isn't a place.
  - A link inside a place to another template there is still refused.
  - An export is named after the template, then numbered from 2; new names are tried up to 999 and no further.
  - → `test_sql_templates_mutation.py`.
- **comments_service.py**:
  - A comment of 4,000 characters, a notebook's 500th thread and a thread's 200th comment are taken; one more is refused, with the limit in the message.
  - A thread keeps the first 500 characters of its lines, and ids are 12 hexadecimal characters.
  - A range of one line, or of all of them, is taken; one from line 0, one that runs backwards, or one of `true` or text is refused.
  - Signed out with nobody known to the sign-in, a comment is written as this PC.
  - Resolve resolves unless told otherwise.
  - This PC's id is made together with its folder, and a second first comment waits for the first one's id.
  - → `test_notebook_comments_mutation.py`.
- **fabric_compute.py**:
  - The bundle's `config.json` is sorted and indented, so the same settings always give the same bundle hash.
  - The runner notebook is nbformat 4.5.
  - Run parameters send empty text for no entities and no backfill.
  - One entity runs alone unless cascade is asked for, and the backfill asked for reaches the engine.
  - The results folder is made with its parents, and the results file is indented.
  - A temporary copy that can't all be removed never fails a finished run.
  - → `test_fabric_compute_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `comments_service.py` | 110 | `return ""` dropped (returns `None`) | Equivalent: this PC's id is only used as a condition and in `f"pc:{pc}" if pc else ""`, where `None` and `""` act the same. |
| `comments_service.py` | 127 | `time.monotonic() > deadline` to `>=` | Equivalent: the wait stops at the deadline either way. The two differ only if the clock reads exactly the deadline, and then the next read, 20 ms later, is past it. |

# Mutation testing, batch 80

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- Connections for the Data Factory page: the cloud and gateway connections the person can use, and what uses each one in each environment's workspace (`fabric_connections_service.py`).
- Pipeline, copy job and dataflow runs for Monitor > Runs, and one run's activities (`fabric_pipeline_runs_service.py`).
- The Direct Lake check's service half: a Delta table's numbers from its log and Parquet footers, relationship keys, and the check itself (`model_directlake_service.py`).

The run was at `main` 1b1e47552 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), so every run was made with `PYTHONDONTWRITEBYTECODE=1`. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fabric_connections_service.py` | test_fabric_connections, test_fabric_connections_service_edges, test_workspace_dataflows (+ test_fabric_connections_mutation) | 79 of 79 (79 + 0) | 70 / 9 | **88.6%** | 70/79 → 78/79 | none | 78 / 1 | **98.7%** | 1 equivalent |
| `fabric_pipeline_runs_service.py` | test_fabric_pipeline_runs, test_fabric_pipeline_runs_service_edges, test_remote_text_lines (+ test_fabric_pipeline_runs_mutation) | 105 of 105 (104 + 1) | 84 / 21 | **80.0%** | 84/104 → 100/104 | 0/1 → 1/1 | 101 / 4 | **96.2%** | 4 equivalent |
| `model_directlake_service.py` | test_model_directlake, test_model_directlake_service_edges (+ test_model_directlake_service_mutation) | 104 of 104 (101 + 3) | 92 / 12 | **88.5%** | 91/101 → 100/101 | 1/3 → 2/3 | 102 / 2 | **98.1%** | 2 equivalent |

No bugs were found in these modules.

### What the survivors were, and the tests that now kill them

- **fabric_connections_service.py**:
  - A connection row keeps its path and privacy level as Fabric gives them, and a connection without details has empty fields.
  - Environments come in the order dev, test, prod, then any other environment by name.
  - At most 500 definitions are read in one scan, and the rest are counted as skipped.
  - The scan runs on a daemon thread with the three tokens.
  - A long workspace or item id in what was missed is cut to 100 characters.
  - → `test_fabric_connections_mutation.py`.
- **fabric_pipeline_runs_service.py**:
  - Times are parsed to the exact microsecond, and a value that isn't text is no time.
  - A duration is rounded to the millisecond.
  - Activity text is capped at 16,000 characters: exactly 16,000 isn't marked as cut, and an empty value isn't either.
  - Activity JSON is indented by two spaces and keeps letters such as ü as they are.
  - An activity row keeps its start and end times, and an error with only a code is still shown.
  - A run with no times is looked up over the last day, with five minutes either side.
  - At most 20 runs per item and 300 in all are listed, newest first.
  - The scan runs on a daemon thread, and a long id in what was missed is cut to 100 characters.
  - → `test_fabric_pipeline_runs_mutation.py`.
- **model_directlake_service.py**:
  - Row groups are sampled across the table, every second file when half the footers are read, and the total is scaled up.
  - Row groups are exact when every footer is read.
  - At most 200 footers are read by default.
  - A Delta log without record counts leaves the row count unknown.
  - A relationship whose key types differ isn't matched row by row.
  - A model with no Direct Lake tables reports no findings and zero counts, and a model that isn't there is named with its workspace.
  - → `test_model_directlake_service_mutation.py`.

### Left

- **fabric_connections_service.py**, 1 equivalent: the thread pool's 8 workers becoming 9 changes how many definitions are read at once, not what the scan finds.
- **fabric_pipeline_runs_service.py**, 4 equivalent:
  - The thread pool's 8 workers becoming 9, as above.
  - The fraction rewrite's `count=1` becoming `0` (every match): a time has only one fraction, and an offset has no dot.
  - Keeping 7 fractional digits instead of 6, or padding to 7: on Python 3.11 and later, `fromisoformat` takes any number of digits and drops those past the sixth, so the time is the same.
- **model_directlake_service.py**, 2 equivalent:
  - `get_add_actions(flatten=False)`: in deltalake 1.6.6 the path, size and record count are top-level columns either way.
  - `len(sample) < len(paths)` becoming `<=`: when every file is read, the count scaled by the same number of files is the count itself.

# Mutation testing, batch 81

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The optimization assessment of a workspace or a capacity, its read budget, and sharing it (`opportunities_service.py`).
- Databricks job runs and Lakeflow pipeline updates for Monitor (`engines/databricks_jobs.py`).
- Which engine each notebook runs on, switching it, and closing its sessions (`engines/sessions.py`).

The run was at `main` fccfe5bb2 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_rpc_surface.py` also calls `optimization_assess`, but it was left out because two of its tests read the app's own files under `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `opportunities_service.py` | test_opportunities, test_opportunities_share (+ test_opportunities_service_mutation) | 81 of 81 (77 + 4) | 53 / 28 | **65.4%** | 51/77 → 77/77 | 2/4 → 4/4 | 81 / 0 | **100%** | none |
| `engines/databricks_jobs.py` | test_databricks_jobs, test_databricks_jobs_edges, test_databricks_runs_service (+ test_databricks_jobs_mutation) | 133 of 133 (131 + 2) | 106 / 27 | **79.7%** | 106/131 → 130/131 | 0/2 → 1/2 | 131 / 2 | **98.5%** | 2 equivalent |
| `engines/sessions.py` | test_engines, test_engines_service, test_engine_switch_failures, test_kudp_guardrails (+ test_engine_sessions_mutation) | 64 of 64 (64 + 0) | 62 / 2 | **96.9%** | 62/64 → 64/64 | none | 64 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **opportunities_service.py**:
  - A capacity reads at most 25 workspaces, in name order ignoring case, and says how many were left out (none for one workspace).
  - One workspace keeps the id, name and capacity Fabric gave, or the id asked for when Fabric gives none.
  - Days are kept between 1 and 90, and default to 30; the demonstration also uses 30.
  - Past 300 items the rest are listed as not read; a small workspace has no problem.
  - Reads run on four workers.
  - The read budget allows exactly its calls, stops at its deadline even with calls left, and reports 1,500 calls, not reached, when unspent.
  - A workspace, its definitions or its histories past the budget, or that can't be read, are reported as not read, never raised.
  - An assessment is kept under its run id; five are kept, and a sixth drops the oldest.
  - Sharing rebuilds the rows from the kept assessment and passes approve and confirm through; one that isn't kept says so and shares nothing.
  - → `test_opportunities_service_mutation.py`.
- **engines/databricks_jobs.py**:
  - At most 100 job runs and 50 pipelines are read, with 5 updates each.
  - A task's error is cut to 1,000 characters, and a run page or notebook path to 500.
  - A state with no words of its own is title-cased, or reads as Unknown.
  - A missing or negative time is 0, and a run without a start or an end took nothing.
  - Compute is Serverless with an environment or no task list; a run whose tasks name no compute uses its own.
  - A failed run without a failed task says only "Failed".
  - A pipeline update took 0 s and says its trigger, and one made exactly at the window's start is kept.
  - Fewer than one day reads one day, and a failed sign-in marks both lists not ok.
  - → `test_databricks_jobs_mutation.py`.
- **engines/sessions.py**:
  - A session that can't be closed, on an engine no longer registered, gets the short local note.
  - The logged close failure is cut to 300 characters.
  - → `test_engine_sessions_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/databricks_jobs.py` | 172 | default `days=1` to `0` | Equivalent: `list` keeps days between 1 and 30, so a default of 0 reads one day too. |
| `engines/databricks_jobs.py` | 77 | `end >= start` to `end > start` | Equivalent: when the end equals the start, both give 0 seconds. |

# Mutation testing, batch 82

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- Notebook files on this PC: where a notebook's file is, which notebook a file path names (for VS Code and kratosforge:// links), delete and restore, and a Fabric notebook saved here (`notebook_files_service.py`).
- What an Azure Databricks workspace offers: warehouses, clusters, the Unity Catalog metastore and catalogs, what to try first, and the summary for the command line (`engines/databricks_discovery.py`).
- The central lakehouse's tables and their migrations (`optimize/schema.py`).

The run was at `main` e88bc60e9 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), so every run was made with `PYTHONDONTWRITEBYTECODE=1`. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run. None of the three modules changed between e88bc60e9 and the `main` this branch was last brought up to.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/` (`test_vscode_setup.py`, which reads `docs/`, was left out).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_files_service.py` | test_audit, test_editor_channel, test_notebook_files, test_notebook_files_service_edges, test_notebook_folders, test_notebook_resources (+ test_notebook_files_service_mutation) | 89 of 89 (87 + 2) | 66 / 23 | **74.2%** | 66/87 → 86/87 | 0/2 → 2/2 | 88 / 1 | **98.9%** | 1 equivalent |
| `engines/databricks_discovery.py` | test_databricks_discovery, test_databricks_discovery_replies, test_engine_connections_settings, test_cli_tenant_commands (+ test_databricks_discovery_mutation) | 91 of 91 (89 + 2) | 70 / 21 | **76.9%** | 70/89 → 87/89 | 0/2 → 2/2 | 89 / 2 | **97.8%** | 2 equivalent |
| `optimize/schema.py` | test_access_cache, test_capacity_history, test_optimize_lakehouse_stores, test_optimize_roles, test_optimize_service, test_optimize_service_mutation, test_optimize_setup, test_optimize_shared, test_optimize_shared_edges, test_tenant_history (+ test_optimize_schema_mutation) | 57 of 57 (55 + 2) | 56 / 1 | **98.2%** | 54/55 → 55/55 | 2/2 → 2/2 | 57 / 0 | **100%** | none |

No bugs were found in these modules.

### What the survivors were, and the tests that now kill them

- **notebook_files_service.py**:
  - A path's reason for not opening starts at 260 characters and stops past 32,767, and a path that isn't a notebook gets no reason.
  - Only a symbolic link or a reparse tag with the name-surrogate bit counts as a link; a OneDrive placeholder doesn't.
  - A path that isn't text, or holds a NUL character, is never local. Off Windows, a relative path and a `//host` or `/\host` path aren't either.
  - A relative path has no name, even when the current folder is the notebooks folder.
  - A missing part, or a link, below the notebooks folder stops the walk.
  - A folder named like a notebook has no name.
  - Only a link to the same item in the same workspace, whose notebook is still here, counts as already here.
  - A copy of Fabric's version is saved where the notebook is, unless a place is given.
  - → `test_notebook_files_service_mutation.py`.
- **engines/databricks_discovery.py**:
  - A name of exactly 200 characters is kept, and one past it is cut to 200 with the `…`. An error in the summary is cut the same way at 600.
  - A cluster with auto-termination has no note, and one without has one.
  - An access mode that isn't set is named "not set".
  - After a failed sign-in, every part is not ok.
  - Running compute comes before starting, and starting before anything else.
  - The summary lists 20 catalogs and marks more with ` …`, says which clusters Databricks Connect can use, and names whatever was suggested under "Try first", or leaves the line out.
  - → `test_databricks_discovery_mutation.py`.
- **optimize/schema.py**:
  - A migration can't be changed once made (the dataclass is frozen).
  - → `test_optimize_schema_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebook_files_service.py` | 249 | `getattr(st, "st_reparse_tag", 0)` default to `1` | Equivalent: the default is only used where there is no reparse tag, and `1 & 0x20000000` is 0, the same as `0 & 0x20000000`. |
| `engines/databricks_discovery.py` | 168 | `rank.get(w["state"], 2)` default to `3` | Equivalent: any state other than RUNNING (0) or STARTING (1) ranks after both either way, and all such states rank the same. |
| `engines/databricks_discovery.py` | 171 | `rank.get(c["state"], 2)` default to `3` | Equivalent, as above, for clusters. |

# Mutation testing, batch 83

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Spark's datetime patterns for `date_format`, `from_unixtime`, `to_date`, `to_timestamp` and `unix_timestamp`, as DuckDB SQL (`spark_datetime.py`).
- Databricks `dbutils` for notebooks on this PC: `fs`, widgets, secrets, task values and the notebook context (`dbutils_local.py`).
- Lookup and Get Metadata in local pipeline runs (`pipeline_data.py`).

The run was at `main` 70da27aad (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `dbutils_local.py` has fewer than 150 mutants, so all were run; the other two were sampled to 150.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`, with one exception, handled as follows:
- `test_spark_datetime.py::test_what_still_differs_is_listed_in_the_compatibility_docs` reads `docs/`, so it was left out with `--deselect`.
- From `test_spark_compat.py`, only its four date and time tests were run, by node id. The rest of that file runs no datetime pattern and takes about 30 s per mutant.

A mutant whose run times out counts as killed, as in earlier batches. So does one whose run used up memory and was stopped.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_datetime.py` | test_spark_datetime (one test deselected), test_spark_local, test_sqlcompat_rules_mutation, the four date and time tests of test_spark_compat (+ test_spark_datetime_mutation) | 150 of 291 (142 + 8) | 120 / 30 | **80.0%** | 115/142 → 141/142 | 5/8 → 6/8 | 147 / 3 | **98.0%** | 3 equivalent |
| `dbutils_local.py` | test_dbutils, test_dbutils_parity, test_sql_cell_widgets, test_databricks_notebook_helpers_mutation (+ test_dbutils_local_mutation) | 118 of 118 (117 + 1) | 104 / 14 | **88.1%** | 103/117 → 114/117 | 1/1 → 1/1 | 115 / 3 | **97.5%** | 3 equivalent |
| `pipeline_data.py` | test_pipeline_data, test_pipeline_data_edges, test_pipeline_script, test_pipeline_runner_edges, test_pipeline_gotchas, test_orchestration_runner_mutation (+ test_pipeline_data_mutation) | 150 of 211 (149 + 1) | 134 / 16 | **89.3%** | 134/149 → 149/149 | 0/1 → 1/1 | 150 / 0 | **100%** | none |

No bug was found.

### What the survivors were, and the tests that now kill them

- **spark_datetime.py**:
  - Each pattern letter takes up to the most letters java.time takes (X, x and Z five; S nine; D three; d, h, K, k, H, m and s two; a one) and is refused by name past that. G, M, L, Q, q and E take four and a year six; past that Spark stops too ("Too many pattern letters"), and so does this PC (since #2868; before it they took five and a year 99).
  - The localized offset is written O or OOOO only, and A, the first letter, is read as a letter Spark refuses.
  - The day and the day of the year are padded to the letters given.
  - One to four era letters give the short or full era (five gave the narrow era until #2868 refused them, as Spark does).
  - The localized and ISO offsets come out right in every width, with Z for a zero offset where java.time writes it.
  - An empty pattern gives empty text, and formatting is lenient unless asked to be strict.
  - Reading takes LLLL and yyyy, and refuses LLLLL and an offset of four letters.
  - → `test_spark_datetime_mutation.py`.
- **dbutils_local.py**:
  - A `%fs` drive path is this PC's disk, and other paths are checked as `dbutils.fs` checks them.
  - A widget's label is its name unless one is given, including a combobox.
  - A multiselect value set in the app is kept only when every item is a choice; a dropdown value is one choice, even with a comma in it.
  - A task value is kept under the task running now.
  - The context's user is the Windows user first, then `USER`, then empty.
  - A notebook run without a timeout passes 0.
  - Listing secrets asks each page with a 30-second timeout, sends the API version on the first page only, and stops when Key Vault repeats a page.
  - → `test_dbutils_local_mutation.py`.
- **pipeline_data.py**:
  - A Lookup output of exactly 4 MB is returned, and one byte more fails.
  - A connection type known only at run time passes the check, and `@@` text is not an expression.
  - The check's message names the activity's kind, "a connection" when the connection has no type, and "a dataset" when the dataset has none.
  - Only runs with a Lookup, a Get Metadata or a Script on a lakehouse sign in first.
  - Get Metadata's `exists` and `itemName` don't read the table, and `structure` and `columnCount` each read its schema.
  - A missing table without a schema is named in `dbo`, and a missing file fails unless `exists` is asked.
  - → `test_pipeline_data_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_datetime.py` | 100 | `n <= 1` to `n <= 0` in `_pad` | Equivalent: `_pad` is only called with more than one letter. |
| `spark_datetime.py` | 145 | `n > 1` to `n >= 1` for `d` | Equivalent: one letter then goes through `_pad` with 1, which gives the same unpadded text. |
| `spark_datetime.py` | 162 | `n > 1` to `n >= 1` for `m` and `s` | Equivalent, for the same reason. |
| `dbutils_local.py` | 76 | default `recurse=False` to `True` in `fs.mv` | Equivalent: `recurse` is not used, because folders move whole. |
| `dbutils_local.py` | 292 | default `precise=False` to `True` in `data.summarize` | Equivalent: `precise` is not used. |
| `dbutils_local.py` | 86 | `cp` checks its first 2 arguments as paths, to 3 | Equivalent in practice: the third argument of `cp` is `recurse`, a flag, which the path check lets through. |

# Mutation testing, batch 84

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Engines that run in a Python runtime of their own: locked installs, the worker's environment, the CLI lookup and the worker session (`engines/runtimes.py`).
- Optimize results waiting on this PC for a central lakehouse that couldn't be reached (`optimize/outbox.py`).
- Estate X-ray: the scan over every lakehouse, its findings, their ranking and what a scan shares (`xray.py`).

The run was at `main` e9097a765 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `optimize/outbox.py` has fewer than 150 mutants, so all were run; the other two were sampled to 150. `engines/runtimes.py` and `optimize/outbox.py` have no `<`, `<=`, `>` or `>=` comparison, so they have no boundary mutants. Nothing merged to `main` between that run and this section touches these modules or their tests.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. For `optimize/outbox.py`, the store-wide tests were given by node id, so only their outbox case ran: the crash-kill test, the durability test and one awkward-path test.

A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/runtimes.py` | test_runtimes, test_runtimes_edges, test_programs_from_path_only, test_dbconnect, test_engine_conformance (+ test_runtimes_mutation) | 150 of 162 (150 + 0) | 124 / 26 | **82.7%** | 124/150 → 150/150 | none | 150 / 0 | **100%** | none |
| `optimize/outbox.py` | test_optimize_outbox, test_optimize_service, test_optimize_service_edges, test_optimize_service_mutation, test_optimize_share_analysis, test_store_upgrades, the outbox cases of test_store_crash_kills, test_sqlite_durability and test_awkward_paths (+ test_optimize_outbox_mutation) | 70 of 70 (70 + 0) | 58 / 12 | **82.9%** | 58/70 → 69/70 | none | 69 / 1 | **98.6%** | 1 equivalent |
| `xray.py` | test_xray, test_xray_edges, test_xray_staging, test_optimize_service (+ test_xray_mutation) | 150 of 169 (142 + 8) | 95 / 55 | **63.3%** | 95/142 → 138/142 | 0/8 → 8/8 | 146 / 4 | **97.3%** | 4 equivalent |

No bug was found.

### What the survivors were, and the tests that now kill them

- **engines/runtimes.py**:
  - The time limits are 20 minutes for an install, 2 hours for a cell and 10 seconds to stop after a cancel.
  - pip runs hidden, with its output captured as text, never raising on a failed exit, and its log is whatever it printed on either stream, cut to the last 20,000 characters.
  - A CLI file that can't be run isn't used.
  - The worker starts hidden, with text pipes, in a session of its own outside Windows, and its reader thread never keeps the app open.
  - Cells are numbered from 1, and only that cell's result or error answers it.
  - A closed session refuses a cell, and after a cell there is nothing to cancel.
  - A cell's time is from its start to its answer, and a wait or a cell that runs out names its limit in minutes.
  - Closing asks a process that still runs to quit, does nothing when it has ended and the session is closed, and always marks the session closed.
  - → `test_runtimes_mutation.py`.
- **optimize/outbox.py**:
  - The default limits are 200 items and 30 days, an age in days is counted in seconds, and a cap below 1 still keeps the newest item.
  - One trim that drops an item for its age and one past the cap counts both.
  - A failure inside a write undoes it and is raised.
  - Each failure doubles the wait, to the second, and the wait stops growing at an hour.
  - A lakehouse never seen has dropped nothing.
  - An error is kept to its first 500 characters when an item is added, fails or is dropped.
  - → `test_optimize_outbox_mutation.py`.
- **xray.py**:
  - The thresholds are the documented ones, each tested at its edge: 200 small files is high, 30 days without a write is stale, 30 versions suggests a vacuum, a cost from a cent is listed and from $10 is medium, and only the five costliest notebooks are listed.
  - Only small-files advice makes a small-files finding, and a kilobyte is shown as one.
  - A staging name dated up to a day ahead can be the newest, and one a second later can't.
  - Findings of one severity rank by kind before title, more files rank first, and `counts` counts each kind.
  - A scanner reads 8 at a time, and reports its progress from 0 and every tenth table.
  - A scan reads at most `MAX_TABLES` and says when it stopped, and each lakehouse row adds up its tables.
  - A table whose log can't be read adds nothing and isn't called unidentified, and a health without sizes counts as none.
  - Only a lakehouse with neither tables nor files is empty, and one that can't be listed is skipped, not called empty.
  - What a scan shares leaves out people, costs and findings without a lakehouse, and names a table without a schema by its name alone.
  - → `test_xray_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `optimize/outbox.py` | 193 | the attempts cap in the backoff, 16 to 17 | Equivalent: from 6 attempts the wait is already past the one-hour cap, so a higher exponent changes nothing. |
| `xray.py` | 49 | `return str(n)` dropped in `_size` | Equivalent: the loop always returns at TB, so the line is never reached. |
| `xray.py` | 75 | default versions since vacuum 0 to 1 | Equivalent: both are below the 30 that suggests a vacuum. |
| `xray.py` | 179 | the rank of an unknown kind, 9 to 10 | Equivalent: both rank after every known kind, the last of which is 8. |
| `xray.py` | 274 | `zip(..., strict=True)` to `strict=False` | Equivalent: the lakehouses walked and their listings are always the same length. |

# Mutation testing, batch 85

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The Python engine's session on this PC: its own kernel process behind the engine contract (`engines/local_python.py`).
- Notebook steps of a local pipeline run, in a fresh kernel with the proof runtime bound (`pipeline_notebooks.py`).
- The DataFrame methods added for Fabric notebook parity: `freqItems`, `inputFiles`, `to`, `withMetadata` and `writeTo` (`spark_df_more.py`).

The run was at `main` 92c08926f (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

The first `spark_df_more.py` run used only `test_spark_df_more` and `test_spark_api_mutation`. Three more test files also call the module (`test_spark_api_fails_cleared`, `test_prove_spark`, `test_prove_spark_install`), so it was run again with them, and the "before" figures below are from that run.

A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/local_python.py` | test_engines, test_engine_registry_mutation (+ test_local_python_mutation) | 50 of 50 (49 + 1) | 46 / 4 | **92.0%** | 45/49 → 49/49 | 1/1 → 1/1 | 50 / 0 | **100%** | none |
| `pipeline_notebooks.py` | test_pipeline_notebooks, test_pipeline_notebooks_edges, test_pipeline_script, test_fabric_step, test_notebook_cells, test_pipeline_data (+ test_pipeline_notebooks_mutation) | 140 of 140 (138 + 2) | 122 / 18 | **87.1%** | 121/138 → 138/138 | 1/2 → 2/2 | 140 / 0 | **100%** | none |
| `spark_df_more.py` | test_spark_df_more, test_spark_api_mutation, test_spark_api_fails_cleared, test_prove_spark, test_prove_spark_install (+ test_spark_df_more_mutation) | 109 of 109 (107 + 2) | 76 / 33 | **69.7%** | 76/107 → 106/107 | 0/2 → 2/2 | 108 / 1 | **99.1%** | 1 equivalent |

One bug was found, in `spark_df_more.py`, and is fixed; its test in `test_spark_df_more_mutation.py` now passes:
- `inputFiles` left out a file when any part of its path contains `_delta_log`, not only the Delta log folder itself. So a plain folder named, for example, `orders_delta_log_archive` listed no files. It now skips only the `_delta_log` folder inside the table.

### What the survivors were, and the tests that now kill them

- **engines/local_python.py**:
  - A kernel's first reply with no version is polled from -1, and one with a version is polled from it.
  - A cell's seconds are the time between its start and its end.
  - The engine has no catalog, and says to use the Data panel instead.
  - The process adapter describes the Python engine and makes its session's kernel with the factory it was given.
  - → `test_local_python_mutation.py`.
- **pipeline_notebooks.py**:
  - Only text names map activities to notebooks.
  - Two notebook steps run at once, and a third waits.
  - A cell that isn't contained needs `allow_uncontained`, which is off unless asked for.
  - A step reports its seconds to the millisecond.
  - Stopping a run stops only the kernels still alive.
  - The notes:
    - A run with no steps and no lakehouse has only the notes added after it.
    - A count of steps reads "1 notebook step" or "2 notebook steps".
    - A lakehouse is named by its name, or by its id when it has none.
  - An unmapped step names the notebook id it runs, or says it has none.
  - A cell with no deadline never times out, and one with a deadline times out only once the deadline has passed.
  - A failed cell names the error and its value, or the cell's status when there is no error output. Its message is cut at 2,000 characters.
  - A step on a lakehouse binds the proof runtime with the run's id, the pinned versions and seed 0, and reports the tables it wrote and the run's folder.
  - → `test_pipeline_notebooks_mutation.py`.
- **spark_df_more.py**:
  - `freqItems` takes one name or a non-empty list of names.
  - Its `support` runs from 1e-4 to 1, both ends included.
  - It quotes a column name with a double quote in it.
  - It replaces a view left from before.
  - `inputFiles`:
    - A plan DuckDB can't write as SQL reads no files.
    - A folder lists its files in every subfolder and never a folder.
    - A Delta folder lists only the files its log names. Remote file URIs are kept as they are, and a relative one is joined to the folder.
    - An unreadable Delta log lists the data files and not the log.
    - A glob lists what it matches, and `**` reaches any depth.
    - A path scanned twice is listed once, with its quote unescaped.
  - `to` takes only a StructType or a DDL string, and `withMetadata` takes only a dict.
  - `select` keeps alias metadata when the columns come as a list.
  - A sort by a column nothing has raises DuckDB's error.
  - The select and sort wrappers are installed once, keep the docs, and leave the schema property as it was when installed again.
  - `writeTo` chains through `tableProperty`, `partitionedBy`, `clusterBy` and `options`, and says what each ignored setting does.
  - → `test_spark_df_more_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_df_more.py` | 67 | `m.group(1)` to `m.group(0)` in `inputFiles` | Equivalent: the whole match adds only the scan's name and its opening bracket, which hold no quoted text, so the quoted paths found are the same. |

# Mutation testing, batch 86

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- How HTTPS failures read behind a corporate network: the Windows certificate store, the root-certificate file for gRPC, and the one-line reason for a refused certificate, a proxy, an unreachable server or a timeout, including pip's output (`net_trust.py`).
- Spark's answers for arrays, maps and generators on this PC, and the map forms that need the bound tables' types (`spark_complex.py`).
- Running several notebooks at once from Home: the limits, the background process and its heartbeat, Stop, and old batches (`notebook_batch_service.py`).

The run was at `main` 61d0e49ee (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Tests that read `docs/` or `app/` were left out with `--deselect`:
- `test_spark_complex.py::test_docs_list_what_still_differs` reads `docs/`.
- In `test_corporate_network.py` and `test_offline.py`, the update-check tests load the shell's `app/src-tauri/src/updater.py`, and `test_every_outbound_call_names_a_timeout_or_uses_the_shared_session` reads it too; the other tests in both files ran. `test_clock_skew.py` loads `updater.py` when it is imported, so it was left out whole.

`localdata/tsql_types.py` was scored first as the third module. Its tests killed all 150 sampled mutants (of 204) before any new test, so it was replaced by `notebook_batch_service.py`.

The first `net_trust.py` run used only `test_net_trust`, `test_tls_inspection` and `test_offline_start` (68 / 23). `test_corporate_network` and `test_offline` also call the module, so it was run again with them, and the "before" figures below are from that run.

A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `net_trust.py` | test_net_trust, test_tls_inspection, test_offline_start, test_corporate_network, test_offline (+ test_net_trust_mutation) | 91 of 91 (91 + 0) | 81 / 10 | **89.0%** | 81/91 → 91/91 | none | 91 / 0 | **100%** | none |
| `spark_complex.py` | test_spark_complex, test_spark_higher_order (+ test_spark_complex_mutation) | 106 of 106 (105 + 1) | 89 / 17 | **84.0%** | 88/105 → 97/105 | 1/1 → 1/1 | 98 / 8 | **92.5%** | 8 equivalent |
| `notebook_batch_service.py` | test_notebook_batch, test_notebook_batch_edges, test_notebook_batch_stop, test_regex_anchors (its batch id test) (+ test_notebook_batch_service_mutation) | 130 of 130 (124 + 6) | 91 / 39 | **70.0%** | 89/124 → 121/124 | 2/6 → 6/6 | 127 / 3 | **97.7%** | 3 equivalent |

One bug was found, outside the three modules, and is fixed; its test in `test_spark_complex_mutation.py` now passes:
- `LATERAL VIEW stack(...)` and `LATERAL VIEW json_tuple(...)` gave each row's values as one struct column (`{'col0': 1}`), where Spark gives the values themselves (`1`). The rewrite is in `spark_builtins_sql.py`. In a select list both gave the values. After `LATERAL VIEW` they, and `inline`, now give one column per name.

### What the survivors were, and the tests that now kill them

- **net_trust.py**:
  - A new process hasn't switched to the Windows store yet (checked in a process of its own).
  - A certificate refused for its dates is told apart in any letter case, as a plain `True` or `False`.
  - PEM text splits into one normalised block per certificate, and an unfinished block is dropped.
  - The gRPC root file keeps only X.509 roots that Windows trusts for every purpose or for server sign-in, sorted, with the public list. A stale file is rewritten, and a current one is left alone.
  - A proxy refusal is known by a `ProxyError` class anywhere in the exception's class tree, or by its words.
  - "Couldn't reach the server" needs an HTTP library in the chain, and gives a plain `False` without one.
  - pip's output: the certificate line (with or without the date reason), the proxy line and the unreachable line for each marker, each naming the fix where it should.
  - → `test_net_trust_mutation.py`.
- **spark_complex.py**:
  - A map `size` whose argument holds `rand()` reads it once and still counts the map's keys.
  - `LATERAL VIEW OUTER explode` of a map holding `rand()` reads it once and keeps the empty and NULL rows.
  - `LATERAL VIEW explode` of a map with no table name gets one of its own.
  - `LATERAL VIEW explode_outer` of a map never drops rows.
  - `reverse` of a number or of a map is left to DuckDB, which refuses the map as Spark does.
  - Only the calls the query itself makes are rewritten, and a two-argument call other than `map_concat`, such as a registered UDF, keeps its call.
  - A statement with nothing marked is returned as it is, and `json_tuple` and `stack` in a `LATERAL VIEW` keep their row counts.
  - → `test_spark_complex_mutation.py`.
- **notebook_batch_service.py**:
  - Up to 50 notebooks run at once, and 51 are refused.
  - The parallel count is 2 by default and kept between 1 and 4.
  - The background processes start with no window, the working folder set, and no console. A notebook's own process reads and writes UTF-8 text.
  - A batch is lost only after more than 90 quiet seconds, and a finished batch is never lost.
  - A notebook that ignores Stop is ended once more than 30 seconds have passed, its last output is kept, and it is logged with the grace it had.
  - The last result line is the notebook's result.
  - Seconds are rounded to a tenth, for each notebook, for a notebook ended after Stop, and for a notebook's own run.
  - Notebooks of a batch run side by side, and a batch file without a parallel count runs one at a time.
  - The heartbeat waits 10 seconds and saves only while the batch runs.
  - The heartbeat and the Stop watcher never hold the process open.
  - A batch exactly seven days old is kept, and one a few seconds older goes.
  - Run in Fabric from Home keeps Fabric's changes unless asked.
  - → `test_notebook_batch_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_complex.py` | 80 | `and` to `or` in `_once_arg`'s wrapper check | Equivalent: `_once_arg` is only given what `_once` wrote. That is its own wrapper, or a `CASE` that is neither a bracket nor holds a parenthesis, so both forms find the same part. |
| `spark_complex.py` | 85 | `and` to `or` in `_once_arg`'s field check | Equivalent: `_once_arg` is only called for templates with one part, so the first field is the one named. |
| `spark_complex.py` | 144 | `and` to `or` when picking the `LATERAL VIEW` calls that get the map forms | Equivalent: every other generator here (`inline`, `stack`, `json_tuple`) is already an `UNNEST` of a list, which the typed pass never takes for a map. |
| `spark_complex.py` | 178 | `quote_identifiers=False` to `True` | Equivalent: it quotes names only in the copy that is typed and then dropped; the names still match the lower-case schema. |
| `spark_complex.py` | 178 | `identify=False` to `True` | Equivalent: as above. |
| `spark_complex.py` | 191 | `-1` to `0` as the default id | Equivalent: every marked argument is given an id before the copy is typed, so the default is never read. |
| `spark_complex.py` | 192 | `or` to `and` in "no type" | Equivalent: `annotate_types` gives every node a type, `UNKNOWN` when it can't tell, so the type is never `None`. |
| `spark_complex.py` | 211 | `and` to `or` for the `LATERAL VIEW` map form | Equivalent: the only other node with a map type here is a `reverse` or `sequence` call, and `_lateral_map` only rewrites a node whose parent is a select. A call in a select list has an alias above it. |
| `notebook_batch_service.py` | 131 | `parallel or 1` to `parallel or 0` | Equivalent: `max(1, ...)` raises 0 to 1. |
| `notebook_batch_service.py` | 156 | a missing beat read as `0` to `1` | Equivalent: either is decades before now, so a batch with no beat is lost the same way. |
| `notebook_batch_service.py` | 228 | `missing_ok=True` to `False` for progress files | Differs only if a progress file goes between the listing and its removal, which a test can't arrange without patching the folder listing. |

# Mutation testing, batch 87

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Databricks Asset Bundles: validate, plan and deploy with the person's own Databricks CLI, and the commands a bundle could run on this PC (`engines/databricks_bundles.py`).
- Setting up the central lakehouse: inspecting it, creating and upgrading its tables, and checking access (`optimize/setup.py`).
- Reading and writing the central lakehouse's shared tables: runs, findings, recommendations, local runs and measurements (`optimize/shared.py`).

The run was at `main` 26bde74c1 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `engines/databricks_bundles.py` has 179 mutants, so 150 of them were sampled; the other two modules have fewer than 150, so all were run. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

`orchestration/activities.py` was scored too, with test_activity_registry, test_pipeline_definitions, test_pipeline_gotchas, test_pipeline_runs and the activity-status test of test_pipeline_coverage (the rest of that file reads `docs/`). All 79 of its mutants were killed, so it needed no new tests and isn't in the table.

The first runs of `optimize/setup.py` and `optimize/shared.py` left out some test files that also call them: test_optimize_service_mutation, test_access_cache_service_mutation and test_opportunities_share for `optimize/setup.py`, and test_optimize_shared_edges, test_optimize_service_mutation and test_optimize_local_runs for `optimize/shared.py`. Each survivor of those runs was run again with every calling test file, and the "before" figures below are from those runs.

A mutant whose run times out counts as killed, as in earlier batches. One `optimize/setup.py` mutant timed out in the "before" run (line 95, `missing or not version` to `and`).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/databricks_bundles.py` | test_databricks_bundles, test_databricks_bundles_more_edges, test_databricks_bundles_service (+ test_databricks_bundles_mutation) | 150 of 179 (148 + 2) | 123 / 27 | **82.0%** | 122/148 → 146/148 | 1/2 → 2/2 | 148 / 2 | **98.7%** | 2 equivalent |
| `optimize/setup.py` | test_optimize_setup, test_optimize_shared, test_optimize_service, test_optimize_service_edges, test_optimize_service_mutation, test_access_cache_service_mutation, test_opportunities_share (+ test_optimize_setup_mutation) | 105 of 105 (102 + 3) | 89 / 16 | **84.8%** | 87/102 → 102/102 | 2/3 → 3/3 | 105 / 0 | **100%** | none |
| `optimize/shared.py` | test_optimize_shared, test_optimize_shared_edges, test_optimize_share_analysis, test_optimize_outbox, test_opportunities_share, test_optimize_service, test_optimize_service_edges, test_optimize_service_mutation, test_optimize_local_runs (+ test_optimize_shared_mutation) | 137 of 137 (134 + 3) | 105 / 32 | **76.6%** | 104/134 → 131/134 | 1/3 → 3/3 | 134 / 3 | **97.8%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/databricks_bundles.py**:
  - Validate and plan get a tenth of the deploy time (60 seconds), and deploy gets 600. A CLI that runs out of time says how many minutes it had.
  - The CLI runs with its output captured as text, in the bundle's folder, with no input.
  - A target is production by its mode alone. A target that isn't a mapping has no mode and isn't the default.
  - A command of exactly 300 characters is shown whole, and one of 301 is shortened and counted.
  - The commands digest is 32 characters, and it doesn't depend on the order of keys in the bundle's files.
  - A plan without the `plan` wrapper is read as the rows, and a plan list can name its resources by `key`.
  - A row's kind comes only from a key under `resources` with three parts.
  - The plan digest is 16 characters of the rows with sorted keys.
  - A failure keeps the last 20 lines of the CLI's output, and warnings come from the last 50 lines only.
  - A deploy's log comes from standard output when standard error is empty.
  - → `test_databricks_bundles_mutation.py`.
- **optimize/setup.py**:
  - A text column where a number belongs is a conflict, and only a text column where text belongs is accepted as another string type.
  - A schema version table with no rows, or no table, reads as no versions, and an empty lakehouse has no schema version.
  - Every table with no recorded version is an unfinished setup.
  - A table missing at the latest version is listed both as missing and as one to add.
  - Retry tries five times by default.
  - An upgrade records only the versions it adds and is audited as an upgrade. A first setup is audited as `initialize`.
  - A 401 or 403 means no permission whatever the message, and another status with a plain message doesn't.
  - No read access means no write access either.
  - An error while registering that isn't about permission is raised.
  - → `test_optimize_setup_mutation.py`.
- **optimize/shared.py**:
  - Writes go in batches of 5,000 by default, and a batch is at least one row.
  - A record id is 32 characters, and an item key's workspace is the part before the first slash.
  - Only non-empty text is cleaned of anything credential-shaped.
  - The overhead counters start at nought.
  - Recording local runs or measurements returns how many were sent, and recording none sends nothing.
  - A measurement keeps its period, method, assumptions and reason, and an empty method or reason is stored as no value.
  - Only this installation's runs count as already in the lakehouse.
  - A run without a status is `complete`, and an artifact keeps its item id.
  - A finding keeps its target. Findings of the same severity are most recent first, and findings seen together keep their record order.
  - A recommendation needs a kind. A new one has no state reason until it changes, and a state reason is cut at 500 characters.
  - Recommendations are latest change first, then by id. No recommendations is an empty list.
  - A missing table, by `None`, by "not found" or by an error type named `TableNotFound`, reads as no rows with the table's columns. Any other read error is raised.
  - Rows keep the first of each record, in order.
  - A change made at the same instant as the state it changes is stamped a microsecond later.
  - → `test_optimize_shared_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/databricks_bundles.py` | 118 | `_text(raw, 300)` to `301` for a command shown whole | Equivalent: this branch runs only when the command is at most 300 characters, and `_text` only folds spaces, so the text is never longer than 300 and the limit is never reached. |
| `engines/databricks_bundles.py` | 119 | `_text(raw[:300], 300)` to `301` for a shortened command | Equivalent: the text is already cut to 300 characters before `_text`, so a limit of 301 is never reached. |
| `optimize/shared.py` | 132 | `max(applied, default=0)` to `default=1` before a write | Equivalent: the check refuses only a version above this app's latest, which is at least 1, so 0 and 1 both pass. |
| `optimize/shared.py` | 330 | `maintain_order=True` to `False` when grouping a recommendation's first event | Equivalent: the groups are joined by recommendation id and the result is then sorted by change time and id, which is unique, so the group order never shows. |
| `optimize/shared.py` | 332 | `maintain_order=True` to `False` when grouping a recommendation's latest event | Equivalent: the same final sort by change time and the unique id fixes the order. |

# Mutation testing, batch 88

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Table clones and warehouse snapshots in T-SQL: finding `CREATE TABLE ... AS CLONE OF` and `ALTER DATABASE ... SET TIMESTAMP` in a batch, the clone's `AT` time, and the local engine's refusal (`tsql_clone.py`).
- Python UDFs on this PC's Spark: `udf`, `pandas_udf`, `spark.udf.register` and `call_udf`, each return type's fit and the Arrow type of a `pandas_udf`'s result (`spark_udf.py`).
- Validate notebook's service: starting, cancelling and listing validations, the clean run and its summary, and the saved report (`validate_service.py`).

The run was at `main` e80b1a87c (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839 and #2902, so each mutant ran without a bytecode cache and each run first checked that its tests pass on the unmutated copy. The method is the same as in earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has more than 150 mutants, so 150 were sampled.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used. From `test_tsql_loud_gaps.py`, only `test_each_item_in_the_docs_refused_list_is_refused` ran (it runs the clone and snapshot statements); the file's other tests read `docs/` or don't reach `tsql_clone.py`.

The first `validate_service.py` run was stopped partway by a time limit and was run again whole; the "before" figures below are from the full run. A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tsql_clone.py` | test_tsql_clone, test_tsql_loud_gaps (its clone and snapshot test) (+ test_tsql_clone_mutation) | 150 of 155 (139 + 11) | 125 / 25 | **83.3%** | 120/139 → 139/139 | 5/11 → 11/11 | 150 / 0 | **100%** | none |
| `spark_udf.py` | test_spark_udf, test_spark_api_mutation (+ test_spark_udf_mutation) | 150 of 240 (148 + 2) | 93 / 57 | **62.0%** | 93/148 → 146/148 | 0/2 → 2/2 | 148 / 2 | **98.7%** | 2 equivalent |
| `validate_service.py` | test_validate, test_validate_service_edges, test_regex_anchors, test_integrations_services_edges (+ test_validate_service_mutation) | 150 of 156 (149 + 1) | 119 / 31 | **79.3%** | 118/149 → 147/149 | 1/1 → 1/1 | 148 / 2 | **98.7%** | 2 equivalent |

The two `tsql_clone.py` timeouts (a statement's index never moving on) and one `validate_service.py` timeout (a report's name number never moving on) count as killed, before and after.

One bug was found, outside what the new tests pin, and is fixed; its test in `test_spark_udf_mutation.py` now passes:
- A `pandas_udf` declared to return an `ArrayType` and returning numpy arrays (`s.apply(lambda v: np.array(...))`) gave null for every row, where Spark gives the arrays as lists. A numpy array wasn't turned into a list before its fit was checked, so it didn't fit the array type. A numpy array with dimensions now becomes its list, one value or many.

### What the survivors were, and the tests that now kill them

- **tsql_clone.py**:
  - A clone is a frozen value.
  - A batch the tokenizer can't read (an unclosed string, bracket or comment) has no clone, no snapshot move and an empty refusal.
  - A `CREATE` cut short at the end of a batch, before `CLONE OF`, isn't a clone, and `CLONE OF` with no source is one with no names, refused by name.
  - A source name cut short after its dot keeps the part before it, and the statement ends there.
  - A clone statement ends at its semicolon, or at the end of the batch, and the text after the semicolon is left alone.
  - `SET TIMESTAMP` counts only after `ALTER DATABASE`, from the word right after it up to ten words on.
  - An `AT` time is read to the millisecond: half a second after now is later than now, and a time equal to now is not.
  - → `test_tsql_clone_mutation.py`.
- **spark_udf.py**:
  - A return type that is neither a Spark type nor a string is refused.
  - Each type's fit: a boolean only for a bool; each integer type exactly its range, never a bool or a float; a decimal only for a `Decimal`; any value as its text for a string; a date but not a datetime for a date; a datetime for a timestamp; bytes and bytearray as bytes; a map with its keys and values fitted; a struct from a dict or a sequence of its length.
  - Structs inside a list arrive as rows.
  - Each Spark type's Arrow type, with string for any other.
  - numpy and pandas scalars become Python values, a pandas array its list, and a numpy array its list (once left as it was: the bug above); null is `None`, NaN or NaT, never an array.
  - `udf` of a UDF is that UDF; a UDF's `evalType` is 100 and a `pandas_udf`'s 200.
  - `pandas_udf` called with the function returns the UDF, keeps a return type given by name, refuses a number as the return type after a function, and `functionType` 200 wins over iterator hints.
  - A UDF registers once per connection.
  - `spark.udf.register` returns the UDF, refuses a return type for a UDF made with `udf()`, and Java UDFs are refused.
  - `install` gives Spark 2's `PandasUDFType` numbers.
  - → `test_spark_udf_mutation.py`.
- **validate_service.py**:
  - The newest hundred validations are kept; keeping one again replaces it; the file is indented JSON.
  - Status finds a saved validation by its own id.
  - The list is newest first, twenty by default, with a validation that has no start time last.
  - A failed cell's traceback keeps its last 4,000 characters.
  - The notebook's Runs on setting is read as `pc`, `fabric` or not known.
  - The clean kernel is told its lakehouse, an empty workspace name when there is none, and the lakehouse is sent again only when it or the write guard changes, with the guard as a bool.
  - A start with no lakehouse doesn't sign in, one with a lakehouse does, and the run is a daemon thread.
  - Cancel before the kernel starts is still a cancel, and cancel stops a running kernel.
  - A run with no lakehouse has no sandbox and deletes nothing.
  - A cancelled cell's own time is kept, and a finished cell with no time keeps none.
  - The run's time is the seconds between its start and end, to the millisecond, and a run that took no time says 0.0 s.
  - The summary counts the cells that succeeded before the failure.
  - The environment lists what the code cells import, not the Markdown.
  - The saved report's name has the validation's date and time, and its JSON is indented.
  - Clean up's plan and tidy-ups are returned as they come.
  - → `test_validate_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_udf.py` | 157 | `ignore_index=True` to `False` when joining an iterator `pandas_udf`'s parts | Equivalent: only the values are read (`list(out)`), never the index. |
| `spark_udf.py` | 331 | `return False` to `return None` for a name already registered | Equivalent: `_taken`'s answer is only used as a condition. |
| `validate_service.py` | 64 | `sort_keys=True` to `False` in the clean kernel's lakehouse key | Equivalent: the key is compared only with the one made the same way from the same context, so both sort or neither does. |
| `validate_service.py` | 187 | `id[9:24]` to `id[9:25]` in the report's file name | Equivalent: the extra character is the `-` before the id's hex part, and the name's trailing `-` is stripped. |

# Mutation testing, batch 89

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Procedures, functions and `sp_rename` in local T-SQL: the token scan that refuses a routine by name before its body is read, a call written without `EXEC`, and the rename itself (`localdata/tsql_routines.py`).
- Local T-SQL's table-valued forms: `GENERATE_SERIES`, `TOP (n) PERCENT` and `(VALUES ...) AS v (a, b)` (`localdata/tsql_tables.py`).
- Spark's regular expressions and string results on this PC: Java's pattern and replacement syntax as RE2 reads it, and the string functions whose results differ on DuckDB (`spark_strings.py`).

The run was at `main` 8838a247c (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839 and #2902, so each mutant ran without a bytecode cache and each test set was first run once on the unmutated copy, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `localdata/tsql_tables.py` has fewer than 150 mutants, so all were run; the other two were sampled to 150. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. Three tests of test_tsql_loud_gaps that read `docs/local-data.md` (the refused-list checks) and test_spark_strings's `test_what_still_differs_is_listed_in_the_compatibility_docs`, which reads `docs/sql-compatibility.md`, were left out with `--deselect`; no other test in these sets reads `docs/`, `examples/` or `app/`. No `-k` filter was used.

A mutant whose run times out counts as killed, as in earlier batches. One `localdata/tsql_routines.py` mutant times out before and after (line 239, the name scan's `end += 1` to `end += 0`, which never ends), and two `spark_strings.py` mutants timed out in the "before" run and one in the "after" run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql_routines.py` | test_tsql_routines, test_local_tsql, test_tsql_loud_gaps (+ test_tsql_routines_mutation) | 150 of 276 (144 + 6) | 124 / 26 | **82.7%** | 121/144 → 143/144 | 3/6 → 6/6 | 149 / 1 | **99.3%** | 1 equivalent |
| `localdata/tsql_tables.py` | test_tsql_table_forms, test_tsql_windows, test_local_tsql_more_edges, test_tsql_loud_gaps (+ test_tsql_tables_mutation) | 148 of 148 (141 + 7) | 122 / 26 | **82.4%** | 117/141 → 136/141 | 5/7 → 7/7 | 143 / 5 | **96.6%** | 5 equivalent |
| `spark_strings.py` | test_spark_strings, test_spark_missing_functions, test_spark_function_names, test_spark_sql_names, test_spark_views_text (+ test_spark_strings_mutation) | 150 of 458 (139 + 11) | 108 / 42 | **72.0%** | 103/139 → 131/139 | 5/11 → 9/11 | 140 / 10 | **93.3%** | 10 equivalent |

One bug was found, in `localdata/tsql_tables.py`, and is fixed; its test in test_tsql_tables_mutation now passes:
- `TOP ('150') PERCENT` and `TOP ((ABS(-150))) PERCENT` returned every row. A percent written as text, or in a second pair of parentheses, was checked against 0 to 100 neither when the query was read nor when it ran, where a warehouse stops with "Percent values must be between 0 and 100." Any percent that isn't a number written out is now checked, as a number, when it runs.

### What the survivors were, and the tests that now kill them

- **localdata/tsql_routines.py**:
  - The token scan names a routine as written up to its parenthesis or semicolon (`CREATE FUNCTION dbo.f(@x INT)` is `dbo.f`), and takes `IF` with no `EXISTS` after it as the name.
  - The scan refuses `EXEC (...)` of a string first in a batch or later, and leaves `exec` after a dot (a function's name), and `EXEC` or `DROP` as a batch's last word, for the parser.
  - A system procedure written alone, or a name with a string after it, gets `EXEC` written in; another name alone doesn't.
  - Behind the scan, `statement()` refuses `ALTER PROCEDURE` (a command to sqlglot), with or without a name, and `DROP PROCEDURE` and `DROP FUNCTION IF EXISTS` by their names, and `EXEC` of a string. It leaves another command and `DROP TABLE` alone.
  - A rename `statement()` has made runs through it again unchanged, not taken for `ALTER TABLE ... RENAME`.
  - A new table name with its schema is refused with the name alone to write instead.
  - → `test_tsql_routines_mutation.py`.
- **localdata/tsql_tables.py**:
  - A whole-number start in parentheses is still whole against a decimal stop, so the two are refused as different types. A column start has no literal kind, so a decimal stop runs.
  - A whole-number series is `int` from -2,147,483,648 to 2,147,483,647, and `bigint` one past either end.
  - A series without a stop, and `TOP ... PERCENT` on anything but a `SELECT`, are refused by name. sqlglot's parser stops on both first, so they are tested on trees made by hand.
  - `TOP 100 PERCENT` keeps every row of a table of 150 rows.
  - A percent worked out per query runs from 0 to 100 (0.5, 25 and 100) and stops outside it (-0.5, 100.5 and 150).
  - → `test_tsql_tables_mutation.py`.
- **spark_strings.py**:
  - `\Q...\E` quotes up to `\E` and the pattern goes on after it. A character past ASCII is kept as it is, and the last ASCII character is escaped.
  - A one-letter property (`\pL`, `\PL`) takes just that letter.
  - `\h` and `\v`, and `\H` and `\V`, are a class outside `[ ]` and its characters inside. `\H` and `\V` inside `[ ]` are refused.
  - A `]` first in a class is a character, so the class runs to the next `]`, and a `(` in a class is no group.
  - A flag group that turns a flag off keeps its `-`.
  - `\p{` without its brace, `\0` without an octal number, `\G`, `\c` at the end and `(?<` without a name are refused by name. A refusal with a hint ends "Or run it in Fabric.", and one without says "Run it in Fabric."
  - An escaped character in a replacement is that character, and an escaped backslash stays one. A replacement group past 9 is refused.
  - The DataFrame wrapper rewrites a replacement unless the function reads Java's itself, and passes a call without a pattern straight on. It takes groups 0 to 9 the pattern has, and refuses one past the pattern's groups or past 9, saying which groups to use.
  - `LIKE` reads a backslash as its escape, also in a pattern from an expression, unless another escape is given.
  - → `test_spark_strings_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/tsql_routines.py` | 47 | `tree.this.this if isinstance(tree.this, exp.Schema) else tree.this` negated for a `CREATE` of a routine | Equivalent in practice: the token scan refuses every `CREATE` of a procedure, function or trigger before the statement is parsed, so this branch of `statement()` is never reached through the engine, and sqlglot gives a routine's name as a procedure or function node, never a `Schema`; the mutant only drops the parameters from a name no one sees. |
| `localdata/tsql_tables.py` | 50 | `return ""` to `return None` for a start or stop that isn't a number literal | Equivalent: the kind is only compared with `"float"`, `{"whole", "decimal"}` and `{"whole"}`, which `""` and `None` both fail. |
| `localdata/tsql_tables.py` | 97 | one more step than needed, `+ 2` to `+ 3` | Equivalent: each value is compared with stop, so the extra step is filtered out. |
| `localdata/tsql_tables.py` | 108 | `GREATEST(count, 0)` to `GREATEST(count, 1)` | Equivalent: a count below 1 comes from a step pointing away from stop, and the one value then made is outside stop, so it is filtered out. |
| `localdata/tsql_tables.py` | 186 | `values.expressions and isinstance(...)` to `or`, for a VALUES list's width | Equivalent: sqlglot reads every VALUES row as a tuple, also `(VALUES 1)`, so both give the tuple's width. |
| `localdata/tsql_tables.py` | 187 | a row that isn't a tuple is 1 wide, to 0 | Equivalent: as above, no row reaches it. |
| `spark_strings.py` | 80 | `end < 0` to `end < 1`, and `end < 0` to `end <= 0`, for `\Q` without `\E` | Equivalent: the search for `\E` starts after `\Q`, so it finds nothing (-1) or a position of 2 or more, never 0. |
| `spark_strings.py` | 196 | `c == "?"` to `c != "?"` for a lazy quantifier | Equivalent in effect: it differs only for a quantifier after a lazy one (`a*?+`), which RE2 refuses too ("bad repetition operator"), so both stop. |
| `spark_strings.py` | 199 | `quantified = False` after a lazy quantifier to `True` | Equivalent in effect: as above. |
| `spark_strings.py` | 242 | `end > 0` to `end >= 0` for `${` | Equivalent: `}` is searched for from the `$`, so it is found at a later position or not at all (-1), never at 0. |
| `spark_strings.py` | 256 | `i + 1` to `i + 0` while reading a replacement group's digits | Equivalent: a group of two digits is past 9 and refused, and the loop still stops on the same group number, so the refusal is the same. |
| `spark_strings.py` | 306 | `_around`'s `return new` to `return None` | Equivalent: only `locate` reads it, to choose what to cast to `INT`; without it the inner call is cast instead, and the result is the same `int`. |
| `spark_strings.py` | 344 | `replacement is not None and not NULL` to `or`, for a replacement from the data | Equivalent: a NULL replacement then gets the check for `$` and `\`, which is NULL, so the result is NULL either way, and Spark has no `regexp_replace` without a replacement. |
| `spark_strings.py` | 416 | `len(parts) == 1` to `== 0` in `concat_ws` with one array | Equivalent: DuckDB's `LIST_CONCAT` of one list is that list. |
| `spark_strings.py` | 457 | the NULL arguments of `||` cast to text, negated | Equivalent: the whole expression is then cast to text anyway, so a bare NULL and `CAST(NULL AS INT)` both give a NULL string. |

# Mutation testing, batch 90

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Comparing two outputs of the same table: the rules, whole-row and keyed matching, the largest difference per column and the examples listed (`verify.py`).
- The control-flow activities a pipeline runs on this PC: Set and Append Variable, If, Switch, ForEach, Until, Wait, Fail, Filter, Notebook, the data activities and Execute Pipeline (`orchestration/adapters.py`).
- Side by side: the checksum of what each run wrote, the type names shown, the table rows, the verdict, which side was faster and the cores to match (`side_by_side.py`).

The run was at `main` 8838a247c (2026-11-17), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. All three modules have more than 150 mutants (246, 194 and 202), so 150 of each were sampled. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` reads `examples/`, so it was left out with `--deselect`; no other test in these sets reads `docs/`, `examples/` or `app/`.

The first runs of `verify.py` and `orchestration/adapters.py` used fewer test files: the comparison tests of test_prove (by node id), test_engine_compare_mutation and test_prove_service_edges for `verify.py`, and test_pipeline_adapters_edges, test_pipeline_runner_edges, test_orchestration_runner_mutation and test_pipeline_runs for `orchestration/adapters.py`. Each survivor of those runs was run again with every calling test file, listed in the table, and the "before" figures below are from those runs.

A mutant whose run times out counts as killed, as in earlier batches. One mutant of `orchestration/adapters.py` timed out, in both runs: with the `raise` removed for an Until iteration that failed (line 190), the loop goes on running.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `verify.py` | test_prove, test_engine_compare_mutation, test_prove_service_edges, test_wizard, test_wizard_mutation, test_learn_service (+ test_verify_mutation) | 150 of 246 (148 + 2) | 120 / 30 | **80.0%** | 119/148 → 148/148 | 1/2 → 1/2 | 149 / 1 | **99.3%** | 1 equivalent |
| `orchestration/adapters.py` | test_pipeline_adapters_edges, test_pipeline_runner_edges, test_orchestration_runner_mutation, test_pipeline_runs, test_pipeline_notebooks, test_pipeline_notebooks_edges, test_pipeline_notebooks_mutation, test_pipeline_data_edges, test_pipeline_data_mutation, test_pipeline_script_edges, test_pipeline_script_mutation (+ test_pipeline_adapters_mutation) | 150 of 194 (147 + 3) | 124 / 26 | **82.7%** | 123/147 → 143/147 | 1/3 → 3/3 | 146 / 4 | **97.3%** | 4 equivalent |
| `side_by_side.py` | test_side_by_side, test_side_by_side_edges, test_side_by_side_more_edges, test_side_by_side_cores, test_side_by_side_service_mutation (+ test_side_by_side_mutation) | 150 of 202 (145 + 5) | 136 / 14 | **90.7%** | 133/145 → 142/145 | 3/5 → 5/5 | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **verify.py**:
  - A timestamp unit other than `us`, `ms` or `s` is refused, and so is a key column that isn't in both outputs, by name.
  - Zero examples is accepted, and the row limit defaults to 5,000,000.
  - Whole rows are rounded to the significant digits the relative tolerance leaves: 17 with no tolerance (and the note says so), at least 1 and at most 15 otherwise. A number column is compared as text only when both sides are integers or decimals.
  - A whole-row example says which side has the row and how many more times, whatever the row holds, under this PC's column name. Rows on one side only make the tables differ.
  - With no column in common, only the row counts are compared, in both directions.
  - A keyed example names the side a missing row is on, and a changed row lists only its changed columns, with both values, under every key column.
  - The largest absolute and relative differences are reported only for number columns that changed, an exact (decimal) column included.
  - Timestamps are compared to the unit asked for.
  - Values come back as JSON can hold them (infinity and NaN as text, a decimal or date as text), and a largest difference that isn't a finite number is left out.
  - → `test_verify_mutation.py`.
- **orchestration/adapters.py**:
  - ForEach runs in batches of 20 by default, of `batchCount` when it is a whole number, of 1 when sequential, and of at least 1 and at most 50. It takes up to 100,000 items and no more.
  - If, Switch, ForEach, Until and Execute Pipeline are containers; the other activities aren't.
  - Append Variable, Wait and a finished Until return an empty output.
  - A Switch on a number matches the case written as text.
  - Until runs 10,000 times at most by default. One that is past its deadline, or exactly at it, after its first iteration, or whose first iteration fails, counts one iteration.
  - A timespan reads minutes and days.
  - Only entries with a text key set the pipeline's return value.
  - A Notebook parameter is given as its type (objects and lists as JSON text, `TRUE` as true, `3.0` and `"7"` as whole numbers), a value that isn't its type or a type that isn't known fails, and a parameter name must be a Python name.
  - → `test_pipeline_adapters_mutation.py`.
- **side_by_side.py**:
  - An integer column is summed as an integer unless it is named as a number, and then it matches the same values as decimals.
  - A date and the same date as text give the same checksum, and so does a large string.
  - The checksum is the sum of the row hashes kept to 64 bits.
  - A large string is named `string`, and a timestamp `timestamp`.
  - A table that one side appended to is noted as appended.
  - A run that failed on one side means the results are not the same.
  - A tie is called for this PC, and processing time is compared only when it is shorter than the whole run.
  - A `%%configure` cell asking for no cores leaves a Python notebook's 2.
  - → `test_side_by_side_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `verify.py` | 322 | `row[-2] > row[-1]` to `>=` for an example's side | Equivalent: whole-row examples are only the rows whose counts differ (`na <> nb`), so the two counts are never equal there. |
| `orchestration/adapters.py` | 48 | `append: bool = False` to `True` in the `Ctx` protocol | Equivalent: a `Protocol` stub; its defaults are never used, because the runner's own `set_variable` is the one called. |
| `orchestration/adapters.py` | 52 | `wait: bool = True` to `False` in the `Ctx` protocol | Equivalent, for the same reason. |
| `orchestration/adapters.py` | 157 | the batch of 1 for a sequential ForEach to 0 | Equivalent: the batch is then raised to at least 1, so a sequential ForEach still runs one item at a time. |
| `orchestration/adapters.py` | 329 | `text.count(":") == 2 and "." in ...` to `or` in reading a timespan | Equivalent: every text gives the same number of seconds, or fails the same way, either way. |
| `side_by_side.py` | 55 | `is_string(typ) or is_large_string(typ)` to `and` in the checksum | Equivalent: a text column then falls through to `CAST(... AS VARCHAR)`, which gives the same text and so the same hash. |
| `side_by_side.py` | 137 | `c.get("version", -1)` to `0` for a commit without a version | Equivalent: such a commit is kept only if the version before the run is below its default, and a table's versions start at 0, so neither -1 nor 0 is above it. |
| `side_by_side.py` | 319 | `pc_cores or 1` to `or 0` when this PC's cores aren't known | Equivalent: a positive number of cores wanted is at least 0 and at least 1, so either way the run uses all cores, and a number below 1 is raised to 1 either way. |

# Mutation testing, batch 91

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Pairing with VS Code and approving its writes: the folder check, the write question and its five-minute wait, turning the editor on and off, unpairing, interrupting and the audit entries (`editor_service.py`).
- An environment's drift: the reasons a read failed, comparing library versions, each kind of drift and the summary lines (`fabric_environment.py`).
- Syncing a folder with a Databricks workspace: which local files are listed, the size limit both ways, the paths read, the errors skipped, and the state kept after a push or a pull (`engines/databricks_workspace_sync.py`).

The run was at `main` 705d4bce8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `editor_service.py` has 130 mutants and `fabric_environment.py` 112, so all of them ran; `engines/databricks_workspace_sync.py` has 188, so 150 were sampled. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_rpc_surface.py` also calls `fabric_environment.py` but reads `app/`, so it was left out of that set; no test in these sets reads `docs/`, `examples/` or `app/`.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `editor_service.py` | test_editor_service_edges, test_editor_channel, test_editor_channel_edges (+ test_editor_service_mutation) | 130 of 130 (130 + 0) | 108 / 22 | **83.1%** | 108/130 → 129/130 | none | 129 / 1 | **99.2%** | 1 equivalent |
| `fabric_environment.py` | test_fabric_environment (+ test_fabric_environment_mutation) | 112 of 112 (112 + 0) | 104 / 8 | **92.9%** | 104/112 → 112/112 | none | 112 / 0 | **100%** | none |
| `engines/databricks_workspace_sync.py` | test_databricks_workspace_sync, test_databricks_workspace_sync_edges, test_databricks_sync_service (+ test_databricks_workspace_sync_mutation) | 150 of 188 (147 + 3) | 121 / 29 | **80.7%** | 121/147 → 142/147 | 0/3 → 1/3 | 143 / 7 | **95.3%** | 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **editor_service.py**:
  - A path is inside the folder only when it is text, the folder isn't empty and has no NUL, and a sibling folder whose name starts the same way is outside.
  - A write question's id is `wq_` and 16 hex digits, and its view gives its fields.
  - With no policy, no editor is named as allowed.
  - Syncing while off starts no channel, and syncing while on keeps the same channel.
  - Turning the editor on or off and deciding a write return what they should.
  - Turning the editor off runs on a daemon thread named `editor-off`, and it is no longer stopping afterwards.
  - Unpairing and interrupting return what they should and are audited with the window and the result.
  - A run's audit gives its seconds.
  - A lakehouse is used only when both its ids are given.
  - A write waits 300 seconds for an answer, and the message says "within 5 minutes"; an approved write is audited as such.
  - → `test_editor_service_mutation.py`.
- **fabric_environment.py**:
  - A 401 or 403 reads as an access problem, and a 404 and a 402 each have their own reason.
  - A version or a specifier that pip can't read is compared as text, and a pre-release meets a specifier.
  - Each kind of drift on its own makes the environment drifted.
  - "No drift" comes before the staged lines and before "Runtime not read" in the summary, and one library is "1 published library".
  - → `test_fabric_environment_mutation.py`.
- **engines/databricks_workspace_sync.py**:
  - Only the first 1000 entries of a folder are looked at.
  - A file of exactly 10 MB is pushed, and one byte more is skipped.
  - A file written locally gets its parent folders.
  - The workspace path read for each form of remote path.
  - A missing path (`RESOURCE_DOES_NOT_EXIST` or `404`) is skipped, and any other error is raised.
  - A missing modified time counts as 0.
  - A `.py` push loses nothing.
  - A pushed file the workspace doesn't list is remembered at time 0, and isn't overwritten.
  - A pull keeps the other files' state, and a file the second listing leaves out gets time 0.
  - Content at the pull size limit is pulled, and content one byte over is skipped with "It's over 10 MB in Databricks, so it isn't pulled."
  - → `test_databricks_workspace_sync_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `editor_service.py` | 77 | `and` to `or` in the root check | Equivalent: both conditions give the same answer for every path form, on POSIX and on Windows. |
| `engines/databricks_workspace_sync.py` | 61 | the `raise` removed for a `/Repos` path | Equivalent: the remote path pattern only accepts `/Users` and `/Shared` paths, so this line is never reached. |
| `engines/databricks_workspace_sync.py` | 131 | `follow_symlinks=False` to `True` in reading a file's size | Equivalent: links are already skipped by `is_file(follow_symlinks=False)` before the size is read. |
| `engines/databricks_workspace_sync.py` | 151 | `len(parts) > 1` to `> 0` | Equivalent: a valid remote path always has more than one part. |
| `engines/databricks_workspace_sync.py` | 151 | the default of 1 to 0 in the parts lookup | Equivalent: a valid remote path always starts with `Users` or `Shared`, so the default is never used. |
| `engines/databricks_workspace_sync.py` | 151 | `len(parts) > 1` to `>=` | Equivalent, for the same reason as the line above. |
| `engines/databricks_workspace_sync.py` | 342 | `>` to `>=` on the pull size limit | Equivalent: the limit's length isn't a multiple of 4, so no valid base64 has it, and content of that length fails to decode either way. |
| `engines/databricks_workspace_sync.py` | 347 | `is_symlink() or parent != root` to `and` | Equivalent: the local listing never includes a link, and a plain name in the folder always has the folder as its parent. |

# Mutation testing, batch 92

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Protecting CSV cells a spreadsheet would run as a formula: dictionary columns, `string_view` columns and the count of protected cells (`csv_cells.py`).
- Reading Delta tables that use deletion vectors or column mapping: when DataFusion's reader is needed, reading some columns, the memory cap and its message, and deleted positions (`delta_read.py`).
- Excellence assessment scores: the rating floors, the coverage thresholds, dimensions with little or no weight and the confidence levels (`excellence/scoring.py`).

The run was at `main` 9ab1d2721 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all of them ran. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Two tests in `test_delta_profile_matrix.py` read `docs/`, so they were left out with `--deselect`. `test_csv_cells.py::test_every_csv_writer_protects_its_cells` scans the whole engine for CSV writers, takes about 40 s and doesn't call `csv_cells.py`'s functions, so it was left out with `--deselect` to keep each mutant's run short.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `csv_cells.py` | test_csv_cells, test_export_exact, test_model_dictionary, test_shortcut_center (+ test_csv_cells_mutation) | 97 of 97 (97 + 0) | 88 / 9 | **90.7%** | 88/97 → 94/97 | none | 94 / 3 | **96.9%** | 3 equivalent |
| `delta_read.py` | test_delta_read_spark_tables, test_delta_profile_matrix, test_delta_profile_readers (+ test_delta_read_mutation) | 78 of 78 (77 + 1) | 66 / 12 | **84.6%** | 65/77 → 75/77 | 1/1 → 1/1 | 76 / 2 | **97.4%** | 2 equivalent |
| `excellence/scoring.py` | test_excellence (+ test_excellence_scoring_mutation) | 136 of 136 (123 + 13) | 123 / 13 | **90.4%** | 116/123 → 123/123 | 7/13 → 13/13 | 136 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **csv_cells.py**:
  - A dictionary column with nothing to protect is left as it is, and counts no cell.
  - A risky dictionary value that no row uses counts no cell; one that rows use is counted once per row.
  - A `string_view` batch or table with nothing to protect still goes to the writer as plain strings.
  - → `test_csv_cells_mutation.py`.
- **delta_read.py**:
  - Column mapping is found from the table setting alone and from the protocol alone, and a mode of `none` is no column mapping.
  - The whole-read cap is 2 GiB.
  - A read over the cap names the features and both sizes ("2000.0 GB, more than 1000 GB").
  - Some columns of a table with deletion vectors are read in the order asked, through `arrow` and `head`.
  - A table without deletion vectors has no deleted positions (an empty dict).
  - → `test_delta_read_mutation.py`.
- **excellence/scoring.py**:
  - Each rating starts at its floor (90, 80, 70, 50 and 0).
  - A rounded zero is never negative.
  - Coverage exactly at the provisional and rated thresholds.
  - A dimension with a weight of 1 still counts, and one with no weight is left out, also when it holds the only outcomes.
  - Confidence exactly at its thresholds.
  - → `test_excellence_scoring_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `csv_cells.py` | 92, 96, 113 | `zip(strict=True)` to `False` | Equivalent: both sides come from the same schema or batch, so they always have the same length. |
| `delta_read.py` | 82 | `get_add_actions(flatten=True)` to `False` | Equivalent: in deltalake 1.6.6 `size_bytes` is a top-level column either way. |
| `delta_read.py` | 149 | `zip(strict=True)` to `False` | Equivalent: both columns come from the same deletion-vector table, so they always have the same length. |

# Mutation testing, batch 93

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Fabric hints, query labels and statistics statements in local T-SQL: the text pass that rewrites what sqlglot can't read, and the tree pass that leaves out or refuses each hint by name (`localdata/tsql_hints.py`).
- The savings waterfall for one run: each optimization's layer, this PC's share, what is left unattributed, gross, overhead and net, and whether the result is complete (`savings/attribution.py`).
- Which Windows account is on the other end of a loopback connection to the local API: the TCP tables, the token's user SID, and the checks built on them (`integrations/peer.py`).

The run was at `main` 7be2906d5 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `localdata/tsql_hints.py` has 313 mutants, so 150 were sampled; `savings/attribution.py` (112) and `integrations/peer.py` (145) were run whole.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter or `--deselect` was needed: no test in these sets reads `docs/`, `examples/` or `app/`. `test_editor_channel.py` only replaces `peer`'s check with a stand-in, so it isn't in the `integrations/peer.py` set.

A mutant whose run times out counts as killed, as in earlier batches. Four `localdata/tsql_hints.py` mutants timed out in both runs, each a loop that no longer moves on: `j += 1` to `+= 0` in the OPTION list (line 131), `i += 1` to `+= 0` for a table called `statistics` (line 93) and for a token that isn't a hint (line 105), and the `raise` removed for an unknown statistics option (line 226). Two more time out in the "after" run: `i = end + 1` to `end - 1` and to `end + 0` (line 111), which an OPTION list that isn't closed now reaches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql_hints.py` | test_tsql_hints, test_warehouse_queries, test_tsql_time_travel, test_tsql_ctes (+ test_tsql_hints_mutation) | 150 of 313 (139 + 11) | 126 / 24 | **84.0%** | 121/139 → 139/139 | 5/11 → 11/11 | 150 / 0 | **100%** | none |
| `savings/attribution.py` | test_savings_attribution (+ test_savings_attribution_mutation) | 112 of 112 (106 + 6) | 87 / 25 | **77.7%** | 86/106 → 104/106 | 1/6 → 6/6 | 110 / 2 | **98.2%** | 2 equivalent |
| `integrations/peer.py` | test_integrations_peer, test_integrations_peer_calls (+ test_integrations_peer_mutation) | 145 of 145 (145 + 0) | 112 / 33 | **77.2%** | 112/145 → 145/145 | none | 145 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/tsql_hints.py**:
  - A batch the tokenizer can't read, or an UPDATE STATISTICS whose rest it can't read, is given back as written for the parse to report.
  - PRINT is a command token, but not UPDATE STATISTICS, so a PRINT that mentions statistics is left alone.
  - A batch that ends in OPTION, or in CREATE or DROP, and an OPTION list that isn't closed, are left for the parse.
  - An UPDATE of a table called `[statistics]` is an update, not UPDATE STATISTICS.
  - An OPTION list with nothing to carry is left exactly as written. A carried hint keeps the items beside it as written, one-word items such as RECOMPILE included, before it or after it.
  - CREATE STATISTICS without a name, or that stops after its name or its table, and DROP STATISTICS with nothing after it, are refused with the local engine's message.
  - In the tree pass, MAXRECURSION is kept for the CTE check when a label beside it is left out, an index hint is refused by its name alone (`INDEX`, not `INDEX(ix)`), and a carried plan hint's note names the hint.
  - → `test_tsql_hints_mutation.py`.
- **savings/attribution.py**:
  - A scenario figure, an unknown classification, a run time that isn't a time, a run that ran somewhere other than Fabric or this PC, and a local basis other than original or optimized are each refused in their own words.
  - A Fabric run without a baseline has no local layer, and a run on this PC without one does.
  - An optimization implemented at the very moment of the run applies; one reverted at that moment doesn't.
  - An optimization that changed nothing is an accrual of 0, not a regression, and a saving or a regression under one CU hour is still marked as one.
  - This PC avoiding exactly what remains isn't marked as capped; avoiding more is.
  - An unknown layer leaves the result incomplete, even with the overhead known.
  - Every figure is rounded to six decimals, gross included when it is capped at a baseline that isn't.
  - → `test_savings_attribution_mutation.py`.
- **integrations/peer.py**:
  - The call tests' stand-in for Windows checks the engine against the module's own constants, so a wrong constant passed. The documented Win32 values (address families, table classes, the established state, the loopback address, error codes, access rights and the TokenUser class) are now written out as numbers.
  - Each table is asked for unsorted, with reserved 0, the right family and class, and a 1-byte probe at size 0 first. A table that keeps growing is asked for five times, then refused.
  - Another account's process is opened for limited query only and not inherited; its token is opened for query, and asked its size first with no buffer and length 0.
  - A token without a valid SID is an OSError with errno 0.
  - Only `win32` is checked, and no listener on the port is a plain `False`.
  - → `test_integrations_peer_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/attribution.py` | 58 | the strength of `unknown` from 2 to 3 | Equivalent: the weakest classification is the one with the highest strength, and `unknown` is still the highest. |
| `savings/attribution.py` | 218 | `complete = False` to `True` when the overhead isn't known | Equivalent: the result's `complete` is `complete and o is not None`, which is false whenever this line runs. |

# Mutation testing, batch 94

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What KratosForge can do with each pipeline activity type: the rules an entry must meet, the classification order and the compatibility report for a pipeline (`orchestration/registry.py`).
- Failure replay bundles: the code and its notes, the inputs and their hashes, taking ids out, writing and reading the bundle file with its name and size checks, and the replay verdicts (`replay.py`).
- A kernel session per notebook: lookups, the session cap, idle sessions, renaming, stopping, deleting, shared sessions and the Sessions list (`kernel_sessions.py`).

The run was at `main` 7be2906d5 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `replay.py` has 203 mutants, so 150 were sampled; the other two have fewer than 150 (101 and 148), so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Three tests read `docs/` and were left out with `--deselect`: `test_activity_registry.py::test_example_report_in_docs_is_current`, `test_pipeline_coverage.py::test_the_page_is_up_to_date` and `test_pipeline_coverage.py::test_the_page_is_in_the_docs_site`. No other test in these sets reads `docs/`, `examples/` or `app/`.

The new tests for `kernel_sessions.py` use stand-in kernels, so they start no engine and no process. Seven of its survivors were run again with `--only` after the last tests were added, with the same test files, and the "after" figures include that run. No mutant timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/registry.py` | test_activity_registry, test_pipeline_coverage, test_orchestration_runner_mutation, test_pipeline_gotchas, test_pipeline_adapters_mutation (+ test_orchestration_registry_mutation) | 101 of 101 (100 + 1) | 87 / 14 | **86.1%** | 86/100 → 98/100 | 1/1 → 1/1 | 99 / 2 | **98.0%** | 2 equivalent |
| `replay.py` | test_replay, test_replay_edges, test_prove_service_mutation (+ test_replay_mutation) | 150 of 203 (144 + 6) | 125 / 25 | **83.3%** | 123/144 → 140/144 | 2/6 → 6/6 | 146 / 4 | **97.3%** | 4 equivalent |
| `kernel_sessions.py` | test_kernel_sessions, test_shared_sessions, test_engines, test_frames, test_wrangler, test_kernel_default (+ test_kernel_sessions_mutation) | 148 of 148 (145 + 3) | 109 / 39 | **73.6%** | 107/145 → 144/145 | 2/3 → 3/3 | 147 / 1 | **99.3%** | 1 equivalent |

One bug was found, in `orchestration/registry.py`: an entry whose planned classification isn't one of the known ones makes `check_entry` raise `KeyError`, instead of listing "planned classification is unknown" with the entry's other problems. `test_an_unknown_planned_classification_is_reported_not_raised` pinned it with `xfail(strict=True)`. Fixed: the marker is gone.

### What the survivors were, and the tests that now kill them

- **orchestration/registry.py**:
  - A pipeline's overall classification is the less capable of any two neighbouring classifications, in either order, and `static` for a pipeline with no activities.
  - A plan one step more capable than today's classification is accepted; a plan as capable or less capable is refused.
  - `local` or `simulated` with no adapter at all is refused.
  - A pipeline runs on this PC only when every activity is `local` or `simulated`, and the others are listed as needing Fabric capacity.
  - A row is verified only when its evidence is `fabric_tested`.
  - `StaticAdapter.execute` refuses to run, `register` returns the entry, and the registry's records can't be changed.
  - → `test_orchestration_registry_mutation.py`.
- **replay.py**:
  - Bundles are version 1, a version 1 bundle opens, and a version 2 bundle asks for an update.
  - A sample holds at most 1,000 rows, and sample files are numbered from 1.
  - A failed cell must be a code cell in range. The parameters tag and frozen are kept; a cell that hadn't run, a cell that ran out of order or ran twice, and a cell that held a secret are each noted once.
  - The error is the first error output, with the home folder replaced, unless the home folder is a single character.
  - Inputs are sorted by lakehouse and name; a file is hashed only when both its size and its modified time still match, and a table is marked created only when the notebook wrote it first and it had no version.
  - Ids are taken out of known values, OneLake addresses in either case and whole-word GUIDs, in keys and lists too, longest id first. Only the lakehouse name is kept, and a note is added only when the code changed.
  - A file that isn't a zip, has no bundle.json, holds text that isn't JSON or isn't a bundle is refused. bundle.json and each sample file may be exactly as large as the limit, and no larger.
  - Table and sample names are checked, and a sample file must be in the samples folder and in the zip.
  - A failed exclusive write leaves no file.
  - The tables to pin, the replay verdicts (with ids compared only for a bundle saved without them) and the input differences.
  - → `test_replay_mutation.py`.
- **kernel_sessions.py**:
  - Five sessions are kept by default, and when no number is set; the cap is at least 1, and the note names it.
  - A running session, the open notebook's session and a dead session are never stopped to make room.
  - `any_running` returns true or false and skips the notebook named.
  - A guest kernel runs calls as its notebook, passes `alive` through, and refuses proofs. A session counts as running when a guest's cell is running.
  - In the engine every notebook shares one kernel; in their own process each notebook gets a prepared kernel, and `put` sets which kind from the kernel.
  - Sessions aren't reaped unless Settings ask; one idle for exactly the limit is kept, and one that started running meanwhile is put back.
  - Switching, renaming, stopping, deleting, sharing, leaving and moving a guest to another host.
  - The Sessions list: a live process is polled, a stopped one isn't, and cells run count 0 when the kernel doesn't say.
  - → `test_kernel_sessions_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `orchestration/registry.py` | 52 | the rank of `unsupported` from 4 to 5 | Equivalent: it is still the highest rank, and still below the 9 given to a classification that isn't known. |
| `orchestration/registry.py` | 289 | the rank of a classification that isn't known from 9 to 10 | Equivalent: both are above every rank. |
| `replay.py` | 93 | the hash read size from `1 << 20` to `1 << 21` | Equivalent: the chunk size doesn't change the hash. |
| `replay.py` | 214 | `indent=1` to `indent=0` for bundle.json | Equivalent: only the layout of the text changes; it reads back as the same bundle. |
| `replay.py` | 217 | `missing_ok=True` to `False` when an exclusive write fails | Equivalent: the call created the file, so it is there to remove. |
| `replay.py` | 239 | the version of a bundle with none from 0 to 1 | Equivalent: 1 is the current version, so the bundle opens either way. |
| `kernel_sessions.py` | 186 | `self.in_process and new not in self._sessions` to `or` in rename | Equivalent: in the engine no session is kept under a notebook's name, so nothing moves, and when the new name has a session the check inside the loop moves nothing. |

# Mutation testing, batch 95

Two modules that Data health and incidents depend on, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Data health from the Delta log: freshness, rows written per load against the usual, schema changes and the alerts they raise (`meta/data_health.py`).
- The incident log of an environment: opening, seeing again, recovering, escalating, manual handling, reporting, and the conditional writes behind them (`workloads/incidents.py`).

The run was at `main` 7be2906d (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. `meta/data_health.py` has 146 mutants, so all of them ran; `workloads/incidents.py` has 229, so 150 were sampled. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

The first run of `workloads/incidents.py` used test_incidents and test_incident_failures only. Each survivor was run again with test_incident_desk and test_incident_service_paths added, and the "before" figures are from that run. A mutant whose run times out counts as killed; none timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/data_health.py` | test_data_health (+ test_data_health_mutation) | 146 of 146 (140 + 6) | 119 / 27 | **81.5%** | 118/140 → 140/140 | 1/6 → 5/6 | 145 / 1 | **99.3%** | 1 equivalent |
| `workloads/incidents.py` | test_incidents, test_incident_failures, test_incident_desk, test_incident_service_paths (+ test_incidents_mutation) | 150 of 229 (146 + 4) | 117 / 33 | **78.0%** | 116/146 → 144/146 | 1/4 → 3/4 | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/data_health.py**:
  - The usual is taken from exactly the last 20 loads, and the list shows 21. Exactly 10 readable loads make a usual.
  - One row above a usual of none is high.
  - A table with no loads says so, with nothing unread.
  - A count of 0 is a count.
  - Metrics that aren't JSON, and parameters that aren't an object, are left out.
  - A naive time is read as UTC.
  - Age is rounded to two places, and exactly the promised hours is still fresh.
  - A schema alert names what was added, removed and retyped, with "column" or "columns".
  - A struct column's type is kept as JSON.
  - A table whose only load is version 0 has no earlier version, and a single load after other commits compares with the version just before it.
  - → `test_data_health_mutation.py`.
- **workloads/incidents.py**:
  - A new list is contract 1. A new incident has a 12-hex-digit id, one occurrence, and empty owner, team, criticality and evidence when the signal has none.
  - Seen again counts from 1 when the count is missing.
  - "Still happening" is noted from exactly 900 s.
  - A critical failure becomes P1 from its third occurrence.
  - A time that isn't one counts as the epoch.
  - Exactly 100 events are kept, with the first always kept, and exactly the newest 200 resolved incidents, one with no time counting as the oldest. The list sorts resolved incidents without a time last.
  - A lost race waits 0.05 s longer each time and gives up after 8 tries.
  - Escalation starts at exactly its time and says how long in minutes. It goes by the store's clock when the store has one, and by this PC's clock for an incident opened before it had one.
  - Notes, report titles and report text are cut to 2,000, 200 and 4,000 characters.
  - A resolved incident can't be resolved, acknowledged or given a new priority, and a report's priority must be one of P1 to P4.
  - → `test_incidents_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/data_health.py` | 163 | `distance > 0` to `>=` for high or low | Equivalent: it is reached only for an unusual load, whose distance is beyond the band (at least 0), so it is never 0. |
| `workloads/incidents.py` | 105 | `occurrences or 1` to `or 0` for the P1 rule | Equivalent: a missing count gives 1 or 0, both below 3, so the priority is P2 either way. |
| `workloads/incidents.py` | 116 | `len(events) > KEEP_EVENTS` to `>=` | Equivalent: at exactly the limit, the slice removed is `events[1:1]`, which is empty. |
| `workloads/incidents.py` | 201 | `indent=1` to `indent=0` for the stored document | Equivalent: only the whitespace between lines changes; every reader parses the same document. |

# Mutation testing, batch 96

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Naming the program that holds a file open: the Restart Manager session, its sizes and retries, the error paths, and the message for exactly three programs (`file_locks.py`).
- Platform support levels: each level's name and what the next one needs, the evidence a strength may ask for, which evidence counts as a source, and the docs table's rows (`platforms.py`).
- Run receipts: the store's messages, the list's order and limit, cell defaults, number formats, and the Markdown and HTML reports (`receipts.py`).

The run was at `main` 5f0b593e2 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `file_locks.py` has 74 mutants and `platforms.py` 81, so all of them ran; `receipts.py` has 177, so 150 were sampled.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_platforms.py::test_the_docs_table_is_the_codes` reads `docs/` and `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` reads `examples/`, so both were deselected. `test_prove.py`, `test_release.py`, `test_rollback.py`, `test_regex_anchors.py` and `test_policy_fail_closed.py` also call `receipts.py`; they were left out of its set to keep each mutant's run short.

The Restart Manager is only on Windows, so the `file_locks.py` tests answer its calls with a stand-in for `rstrtmgr.dll` that reads and writes the same ctypes objects, and they swap the module's `sys`, not the process's.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `file_locks.py` | test_file_locks (+ test_file_locks_mutation) | 74 of 74 (73 + 1) | 28 / 46 | **37.8%** | 28/73 → 71/73 | 0/1 → 1/1 | 72 / 2 | **97.3%** | 2 equivalent |
| `platforms.py` | test_platforms (+ test_platforms_mutation) | 81 of 81 (77 + 4) | 62 / 19 | **76.5%** | 61/77 → 76/77 | 1/4 → 2/4 | 78 / 3 | **96.3%** | 3 equivalent |
| `receipts.py` | test_receipts_edges, test_receipts_env, test_prove_service_edges, test_scorecard_cost, test_advisor, test_side_by_side, test_wizard (+ test_receipts_mutation) | 150 of 177 (148 + 2) | 107 / 43 | **71.3%** | 107/148 → 147/148 | 0/2 → 2/2 | 149 / 1 | **99.3%** | 1 equivalent |

One defect was found and is recorded as a strict `xfail` in `test_receipts_mutation.py`: the export's redaction note lowercases each choice, so "This PC's name and hardware" reads "this pc's name and hardware". Fixed: the note now lowercases only each choice's first letter, and the `xfail` marker is gone.

### What the survivors were, and the tests that now kill them

- **file_locks.py**:
  - The library is loaded from System32 only.
  - The session key buffer, the process info's name fields and its field order match Windows' sizes and layout.
  - The one file is registered with no applications or services, and the session is ended with the id it started with.
  - A list that grows is asked for again with room for it, an empty need still leaves room for one, and it is asked three times at most.
  - A failed start is neither used nor ended; a failed registration or listing names no one and ends the session.
  - A program without a name is named by its process id, and each name is given once, in order.
  - A missing Restart Manager names no one, and off Windows the list is empty even when asked directly.
  - Exactly three programs are all named with nothing after them, and four end with "and 1 more".
  - → `test_file_locks_mutation.py`.
- **platforms.py**:
  - Each level's number and name, what the next one needs, and level 5 with nothing next.
  - The evidence each strength asks for is never more than a platform can verify.
  - Platforms and features can't be changed once made, and a feature without evidence names the evidence that counts.
  - Only real evidence is listed as a source, evidence for an area that doesn't apply is ignored, and weaker evidence doesn't lower stronger.
  - The SQL targets are those the SQL profiles know, and pipeline activities are counted by classification.
  - The docs table's rows, including "nothing" when no level is next.
  - → `test_platforms_mutation.py`.
- **receipts.py**:
  - The Markdown and HTML reports line by line, including the title, both tables, the code block and the 12 packages shown.
  - A receipt without a notebook is titled "Notebook", and a verdict without text or level is shown alone.
  - Inputs with only files are listed, and a missing file hash is blank.
  - Cells without ids or kinds are numbered from one and count as code.
  - Canonical JSON keeps text as UTF-8.
  - Whole numbers get thousands separators, and 1e15 is written as an exponent.
  - The store's messages name a receipt by its first 12 characters.
  - The list is newest first with undated receipts last, gives each verdict, and shows 50 unless asked for more.
  - → `test_receipts_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `file_locks.py` | 76 | the needed count's start value 0 to 1 | Equivalent: Restart Manager only writes this value, and it is read only after a call has set it. |
| `file_locks.py` | 76 | the returned count's start value 0 to 1 | Equivalent: it is set to the buffer's length before every call. |
| `platforms.py` | 170 | `>` to `>=` in keeping the strongest evidence | Equivalent: equal evidence replaces itself with the same value. |
| `platforms.py` | 190 | the levels walked from index 1 to 0 | Equivalent: level 0 needs no area, so it is always met and is already the starting level. |
| `platforms.py` | 201 | `<=` to `<` in capping a strength | Equivalent: at equal rank both branches return the same evidence. |
| `receipts.py` | 54 | `allow_nan=False` to `True` | Equivalent: the check before it already refuses NaN and infinity in every dict, list and tuple, and keys that aren't text. |

# Mutation testing, batch 109

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The app's incident calls: the list, the summary, the check and the watch, the agents and queue pages, the product status told to the catalog, and the openings an older app already posted (`incident_service.py`).
- The incident desk: the board's counts, the bell's For you list, reported incidents, who an incident reaches and telling them, and the incident history and measures kept in the central lakehouse (`incident_desk.py`).

The run was at `main` ad1ac4cf8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants (96 and 149), so all of them ran. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter or `--deselect` was needed: no test in these sets reads `docs/`, `examples/` or `app/`. `test_rpc_surface.py` names the desk's calls but reads `app/`, and doesn't call them, so it isn't in the `incident_desk.py` set. `test_dataops.py` calls `incidents_watch` for late and failed tables, so it is in the `incident_service.py` set, as are `test_agent_fleet.py`, `test_workload_queue.py` and `test_run_as.py` for the agents, queue and run-as pages.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `incident_service.py` | test_incident_service_paths, test_incident_partial_work, test_incidents, test_product_status, test_dataops, test_agent_fleet, test_workload_queue, test_run_as (+ test_incident_service_mutation) | 96 of 96 (96 + 0) | 73 / 23 | **76.0%** | 73/96 → 95/96 | none | 95 / 1 | **99.0%** | 1 equivalent |
| `incident_desk.py` | test_incident_desk, test_incident_partial_work (+ test_incident_desk_mutation) | 149 of 149 (148 + 1) | 112 / 37 | **75.2%** | 112/148 → 144/148 | 0/1 → 1/1 | 145 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **incident_service.py**:
  - The list shows resolved incidents unless asked not to, the queue looks 24 hours ahead, and a check or the watch tells Teams only when asked (no outbox is opened otherwise).
  - Acknowledging gives back the acknowledged incident.
  - The check reads the latest 2,000 runs, and the queue the last 100 Fabric jobs as last checked, never asking Fabric for fresh statuses.
  - A OneLake environment's control area is read with the silent storage token, and an empty one when there is none.
  - The incidents list says its agents could be read, and agents that can't be read say why in words, not as a state.
  - The queue and run-as pages say whether the Fabric schedules were never read, are being read, failed (with Fabric's words) or were read (with when).
  - A Fabric schedule shows the environment whose workspace it is in.
  - Only this project's assignments set how critical a table is, and an assignment without a criticality sets none.
  - The watch still tells the catalog the product status when an agent checks the environment, and only a live agent that checks incidents leaves the check to it.
  - An agent offline with no other agent live is red.
  - The log says whether the platform will be asked for the product status again.
  - A late table without evidence is posted, even when an older app's record has a key that looks alike.
  - → `test_incident_service_mutation.py`.
- **incident_desk.py**:
  - The board shows resolved incidents unless asked not to, says which incidents were detected and which reported, and counts as mine only my own, not someone else's.
  - The bell puts breached before at risk before due, and missed before at risk, whatever was seen last, and gives each incident's last-seen time.
  - An incident that is gone says so.
  - A platform that keeps no followers, or an older one without the followers call, still names the owners.
  - Impact shows the latest time people were told; telling says which routes it went to; a list of people too long for one note, already told, is noted once.
  - The central lakehouse is read with schemas unless it says otherwise.
  - History before any copy is empty, whether the table is missing by message or by type; a history that can't be read for another reason is an error.
  - Within one second the later stage of handling wins (an unknown state counts as open), and a snapshot with no time never replaces a dated one.
  - History is newest first, with undated incidents last and no bookkeeping columns; it shows 500 by default, at least one and at most 5,000, and one environment's when asked.
  - Measures read up to 5,000 incidents, and say they were capped at 5,000 exactly.
  - The history copy keeps every timeline entry and the workspace it came from.
  - → `test_incident_desk_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `incident_service.py` | 179 | `split(":", 2)` to `split(":", 3)` for an assignment's table | Equivalent: a project's name (the save gate's pattern) and a table's name (a file name on Windows) have no `:`, so a workload id has exactly three parts either way. |
| `incident_desk.py` | 95 | the bell's rank for any other SLA state from 2 to 3 | Equivalent: every ranked state is 0 or 1, so 2 and 3 sort the same. |
| `incident_desk.py` | 237 | the history's rank for `resolved` from 2 to 3 | Equivalent: it is still the highest, so the same row wins. |
| `incident_desk.py` | 239 | `unique(maintain_order=True)` to `False` | Equivalent: the rows are sorted by opening time right after, and that sort doesn't keep the order of ties either way. |
| `incident_desk.py` | 258 | the measures' history limit from 5,000 to 5,001 | Equivalent: the history itself shows at most 5,000. |

# Mutation testing, batch 110

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Excellence findings: what a finding and an advisory finding carry, their stable id, the ranking and the next actions (`excellence/findings.py`).
- Deleting a folder the way Windows lets it be deleted: busy and read-only files, links, and the retries (`trees.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`. `test_trees.py` reads the engine's own files under `sidecar/`, which the tool copies.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/findings.py` | test_excellence, test_excellence_catalog (+ test_excellence_findings_mutation) | 26 of 26 (26 + 0) | 13 / 13 | **50.0%** | 13/26 → 24/26 | none | 24 / 2 | **92.3%** | 2 equivalent |
| `trees.py` | test_trees (+ test_trees_mutation) | 30 of 30 (29 + 1) | 21 / 9 | **70.0%** | 20/29 → 28/29 | 1/1 → 1/1 | 29 / 1 | **96.7%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/findings.py**:
  - A finding id is `fx-` and 16 hex digits.
  - A failed finding takes its text and evidence from the outcome; an advisory one takes them from the advice, and is empty when there is none.
  - The outcome's detail is kept, and a missing one is empty.
  - With severity tied, stronger evidence comes first, then less effort, then less risk, at each step of each scale.
  - There are five next actions by default.
  - → `test_excellence_findings_mutation.py`.
- **trees.py**:
  - WinError 145 alone makes a folder busy, and another code doesn't.
  - A link is retried without `chmod`, so the file it points to stays read-only.
  - A read-only file is made writable with nothing else added, and a folder also gets its execute bit.
  - What isn't an `OSError` is raised at once, and so is a different failure on a retry.
  - → `test_trees_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/findings.py` | 22 | effort and risk `"high": 2` to `3` | Equivalent: only the order is used, and 3 is still above medium's 1. |
| `excellence/findings.py` | 23 | evidence `"insufficient": 3` to `4` | Equivalent: only the order is used, and 4 is still above inferred's 2. |
| `trees.py` | 53 | `_busy`'s last `return False` to `None` | Equivalent: its only caller reads it as `not _busy(...)`, and `None` and `False` are both false. |

# Mutation testing, batch 111

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Excellence controls: what a control's definition must have, the registry, and running one control on one subject (`excellence/controls.py`).
- The PySpark DataFrame API list: one example run as a notebook cell, a method's status and note, rows compared in any order, and the docs page (`spark_api_coverage.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used.

Four tests in `test_spark_api_coverage.py` were left out with `--deselect`: three read `docs/` (`test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date`, `test_the_docs_link_the_list`) and one reads the repository's `README.md` (`test_the_readme_counts_match_spark_api_coverage`). That is why so many `spark_api_coverage.py` mutants survived before: nothing left in the run called `markdown()`, and the real examples rarely reach the notes or the error paths. The new tests check those on a stand-in kernel.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. One `spark_api_coverage.py` survivor of the "after" run (line 113) was killed by a test added after it; the figures below take that test's own `--only` run for that mutant. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/controls.py` | test_excellence, test_excellence_catalog, test_excellence_scoring_mutation, test_excellence_findings_mutation (+ test_excellence_controls_mutation) | 113 of 113 (112 + 1) | 107 / 6 | **94.7%** | 106/112 → 112/112 | 1/1 → 1/1 | 113 / 0 | **100%** | none |
| `spark_api_coverage.py` | test_spark_api_coverage, test_spark_api_fails_cleared (+ test_spark_api_coverage_mutation) | 100 of 100 (97 + 3) | 41 / 59 | **41.0%** | 41/97 → 96/97 | 0/3 → 3/3 | 99 / 1 | **99.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/controls.py**:
  - A context refuses a kind it wasn't given, with a message saying so.
  - A failure keeps its detail, and the detail reaches the outcome.
  - A control can't be changed once made.
  - A reference needs a title as well as an https url.
  - Registering a control gives it back.
  - → `test_excellence_controls_mutation.py`.
- **spark_api_coverage.py**:
  - Each refusal phrase counts on its own, in any case.
  - An expression is run as the value and kept; a statement, and source Python can't read, run as they are. Text and stream outputs are printed, others aren't.
  - An error is its name and first line, cut to 160 characters; one without text is the status. A value this PC can't collect gives its error's first line.
  - Only one expression is an expression.
  - An example Spark stops on must stop here too; a stop here is refused or fails, with the source and the error.
  - Rows without a set order compare in any order, and ordered rows don't. A different value names both, each cut to 70 characters.
  - A result without printed text is kept. Each table's rows are sorted, its header stays, and a stray border after the tables is left alone.
  - A source is shown on one line, cut after 60 characters.
  - The page counts each status, lists each class that has methods, and escapes `|` and line breaks in a note.
  - The page is written to the repository's `docs/`, and importing the module writes nothing.
  - → `test_spark_api_coverage_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_api_coverage.py` | 144 | `return False` to `None` for source Python can't read | Equivalent: its only caller reads it as a condition, and `None` and `False` are both false. |

# Mutation testing, batch 112

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Formatting notebook cells: what ruff is given, ruff's errors as the cell's reason, `%%sql` cells and cells without ruff (`code_format.py`).
- Undoing a bad load: which Delta versions can still be restored after VACUUM, and the restore itself (`meta/restore.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. In the "after" run of `meta/restore.py` the new test file was given first, so a mutant it kills stops before the slower estate tests. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `code_format.py` | test_code_format (+ test_code_format_mutation) | 61 of 61 (61 + 0) | 51 / 10 | **83.6%** | 51/61 → 59/61 | none | 59 / 2 | **96.7%** | 2 equivalent |
| `meta/restore.py` | test_estate, test_duties (+ test_meta_restore_mutation) | 59 of 59 (56 + 3) | 36 / 23 | **61.0%** | 35/56 → 56/56 | 1/3 → 1/3 | 57 / 2 | **96.6%** | 1 equivalent, 1 not killed |

No bugs were found. One edge is noted under "Left" for `meta/restore.py`.

### What the survivors were, and the tests that now kill them

- **code_format.py**:
  - ruff gets the cell as a one-cell notebook (format 4.5), with its own arguments and a 20 s limit.
  - A parse error names its line and reason; another ruff error is given in ruff's words, also one that says "parse" without a line.
  - A formatter that hangs changes nothing.
  - An `%%sql` cell with nothing under its magic is left alone.
  - A `%%pyspark` cell without ruff says ruff isn't installed.
  - → `test_code_format_mutation.py`.
- **meta/restore.py**:
  - A commit's time is milliseconds since 1970 in UTC, and its rows come from the commit's metrics.
  - 50 versions are listed, and 1,000 commits are read to find the VACUUMs.
  - Without a VACUUM, every earlier version can be restored.
  - A VACUUM reaches back by its own retention, or by the layer's when it has none, exact to the millisecond and the hour. The latest VACUUM's horizon counts.
  - Maintenance commits are marked, and only a version that can't be restored says why.
  - A restore counts the files it restored and removed, and 0 when the counts are missing.
  - Restoring to a version whose files VACUUM removed fails and changes nothing.
  - → `test_meta_restore_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `code_format.py` | 44 | `text=True` to `False` for ruff's run | Equivalent: the run also gives `encoding="utf-8"`, which opens the pipes as text either way. |
| `code_format.py` | 97 | `max_workers=4` to `5` | Equivalent: `pool.map` gives the results in the cells' order whatever the number of workers. |
| `meta/restore.py` | 61 | `cut > horizon` to `>=` | Equivalent: when two VACUUMs give the same cut, either one is the same horizon. |
| `meta/restore.py` | 76 | `superseded > horizon` to `>=` | Not killed: it differs only for a version superseded at exactly the horizon's millisecond. The comment on line 60 says files removed before the horizon are gone, so at that millisecond the mutant may be the better answer. The engine is cautious there (it says such a version can't be restored), and no test was written to fix either answer. |

# Mutation testing, batch 113

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Spark's aggregate results on DuckDB: empty arrays, `count` of several columns, `WITH ROLLUP` and `WITH CUBE`, and the result types of percentiles, `grouping` and `count_if` (`spark_aggregates.py`).
- Spark's window functions on this PC: the int ranks, and the windows Spark stops on without an order or with a frame it doesn't allow (`spark_windows.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_spark_aggregates.py::test_docs_list_what_still_differs` reads `docs/`, so it was left out with `--deselect`.

Every mutant of both modules was killed by the tests already there, so no test was added and there was no "after" run. Neither module has a comparison that a boundary mutant can move. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_aggregates.py` | test_spark_aggregates, test_spark_dataframe_decimals | 30 of 30 (30 + 0) | 30 / 0 | **100%** | 30/30 → 30/30 | none | 30 / 0 | **100%** | none |
| `spark_windows.py` | test_spark_windows | 45 of 45 (45 + 0) | 45 / 0 | **100%** | 45/45 → 45/45 | none | 45 / 0 | **100%** | none |

No bugs were found.

# Mutation testing, batch 114

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- The project files a release keeps in an environment, and restoring them for a run or a rollback (`meta/snapshot.py`).
- The workflow prompts the MCP server offers AI tools: their arguments and how they render (`integrations/prompts.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used.

Four tests in `test_integrations_prompts.py` read `docs/` or `deploy/`, so they were left out with `--deselect` (`test_workflows_page_matches_the_prompts`, `test_connect_page_carries_every_setup_from_the_app`, `test_agent_instructions_name_every_prompt_and_only_real_tools`, `test_kratosforge_skill_names_only_real_tools_and_every_prompt_and_matches_its_page`). Of `test_integrations_conformance.py`, only `test_resources_templates_and_prompts_are_listed_and_readable` was run, the one test there that reads the prompts: the whole file takes about 47 s, and another of its tests reads `docs/`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. In the "after" run of `meta/snapshot.py` the new test file was given first. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/snapshot.py` | test_rollback (+ test_meta_snapshot_mutation) | 39 of 39 (39 + 0) | 30 / 9 | **76.9%** | 30/39 → 37/39 | none | 37 / 2 | **94.9%** | 1 equivalent, 1 not killed |
| `integrations/prompts.py` | test_integrations_prompts, one test of test_integrations_conformance (+ test_integrations_prompts_mutation) | 22 of 22 (22 + 0) | 17 / 5 | **77.3%** | 17/22 → 22/22 | none | 22 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/snapshot.py**:
  - Saving a release says how many files it kept, and 0 the second time.
  - A release the environment doesn't keep is a `LookupError`.
  - A path outside the project, and files that don't add up to the version, are refused, and no half-written folder is left.
  - A folder that doesn't load isn't the release.
  - → `test_meta_snapshot_mutation.py`.
- **integrations/prompts.py**:
  - Each workflow needs every argument it names, and one with none given names them all.
  - A prompt can't be changed once made.
  - → `test_integrations_prompts_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/snapshot.py` | 105 | `_matches`'s `return False` to `None` | Equivalent: both callers read it as a condition, and `None` and `False` are both false. |
| `meta/snapshot.py` | 91 | `ignore_errors=True` to `False` when a failed restore clears its temporary folder | Not killed: the folder is made before the `try` and moved only by its last step, so it is there to delete; the two differ only when the delete itself fails, which a test here can't make happen on Linux without patching `shutil`. |

# Mutation testing, batch 115

One module not covered before, not touched by an open pull request, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- App Studio publishing: the report's name, the readiness check, the fingerprint the confirmation is bound to, and the item definition (`appstudio/publish.py`).

`http_session.py` was planned for this batch but left out because there wasn't time for it before this lane's stop: 17 test files call it, and a fair run needs them all.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/publish.py` | test_appstudio_publish, test_appstudio_publish_edges (+ test_appstudio_publish_mutation) | 55 of 55 (55 + 0) | 51 / 4 | **92.7%** | 51/55 → 54/55 | none | 54 / 1 | **98.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **appstudio/publish.py**:
  - A report name is kept up to 256 characters and cut after that.
  - A field the semantic model doesn't have is named by its component's title.
  - The fingerprint doesn't depend on the order of the specification's keys.
  - → `test_appstudio_publish_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `appstudio/publish.py` | 110 | `ensure_ascii=False` to `True` for the fingerprint | Equivalent: it only changes how other characters are spelled in the JSON that is hashed, so equal content still gives equal fingerprints and different content different ones, and the check and the publish both make it the same way. |

# Mutation testing, batch 116

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Excellence assessment profiles: the severity weights, a profile's fingerprint, what a profile must hold, and a stored profile read back (`excellence/profiles.py`).
- The rule guides beside a best-practice finding: reading a best-practices page into guides, its examples, building them from every page, and the guide for a pipeline warning code (`learn/guides.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` reads `docs/`, so it was left out with `--deselect`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/profiles.py` | test_excellence, test_excellence_catalog, test_excellence_scoring_mutation (+ test_excellence_profiles_mutation) | 102 of 102 (96 + 6) | 88 / 14 | **86.3%** | 87/96 → 96/96 | 1/6 → 5/6 | 101 / 1 | **99.0%** | 1 not killed |
| `learn/guides.py` | test_learn_guides (+ test_learn_guides_mutation) | 56 of 56 (54 + 2) | 40 / 16 | **71.4%** | 40/54 → 50/54 | 0/2 → 2/2 | 52 / 4 | **92.9%** | 4 not killed |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/profiles.py**:
  - The severity weights are 10, 5, 3, 1 and 0, and the baseline uses them.
  - A fingerprint is 16 hex digits, changes with the profile, and is the same for a profile read back.
  - A profile can't be changed once made.
  - A dimension may weigh 0, or less than 1.
  - The coverage thresholds at their edges: a provisional coverage of 0 and a rated one above 1 are refused; equal thresholds, and a rated coverage of 1, are fine.
  - A stored profile keeps its severity overrides and disabled controls.
  - → `test_excellence_profiles_mutation.py`.
- **learn/guides.py**:
  - Text before the first rule heading isn't a guide.
  - A title line with one or two tags leaves the area or the importance empty, and a later bold line changes nothing.
  - A note on a Before or After mark is kept as a comment.
  - Building reads every page and gives the guides in rule order.
  - The shipped guides are read only when none are given.
  - → `test_learn_guides_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/profiles.py` | 59 | weights' `abs(sum - 100) > 1e-9` to `>=` | Not killed: it differs only for a sum exactly 1e-9 away from 100, which the slack is there to absorb; no test was written for that one value. |
| `learn/guides.py` | 121 | four mutants of the `__main__` block (`+ "\n"`, `sys.argv[1]`, `indent=1`, `ensure_ascii=False`) | Not killed: this is the build script run by hand to write the shipped `rule_guides.json` (as `test_state_writes.py` notes); a test would have to run it and rewrite that file. |

# Mutation testing, batch 117

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Evidence from what the app already reads: the workspace and its items, role assignments, the capacity and the optimization engine's result (`excellence/adapters.py`).
- Where the optimization tables live, in OneLake or in a folder on this PC: listing tables and schemas, and adding columns (`optimize/lakehouse.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. Neither module has a boundary mutant. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

`optimize/lakehouse.py` is also reached through the Optimize service, by test_optimize_service, test_optimize_service_edges, test_access_cache, test_incident_desk, test_opportunities_share, test_tenant_history and the two earlier Optimize mutation files. All of them together take about 105 s, so the run used the five files that call the stores most directly (about 50 s). A mutant whose run times out counts as killed; none timed out.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/adapters.py` | test_excellence_catalog, test_excellence (+ test_excellence_adapters_mutation) | 55 of 55 (55 + 0) | 48 / 7 | **87.3%** | 48/55 → 55/55 | none | 55 / 0 | **100%** | none |
| `optimize/lakehouse.py` | test_optimize_lakehouse_stores, test_optimize_schema_v2, test_optimize_setup, test_optimize_shared, test_optimize_sync_states (+ test_optimize_lakehouse_mutation) | 41 of 41 (41 + 0) | 40 / 1 | **97.6%** | 40/41 → 41/41 | none | 41 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/adapters.py**:
  - A workspace without a display name is named by its `name`.
  - A missing description is empty text, and a given one is kept.
  - A listed item needs both an id and a type, and a missing item name is empty text.
  - A role whose principal has no id, or no role, reads as empty text.
  - A capacity's missing state, SKU and region read as empty text.
  - An unread peer without a name is named by its item id.
  - → `test_excellence_adapters_mutation.py`.
- **optimize/lakehouse.py**:
  - Adding columns returns the names of the columns the table didn't have, and none when it had them all.
  - → `test_optimize_lakehouse_mutation.py`.

### Left

None.

# Mutation testing, batch 118

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Evidence records for the excellence assessment: canonical text, subjects, records and their ids, and the bundle that keeps bad records out (`excellence/evidence.py`).
- The audit trail for external AI tools: the line for each call, the rows the store keeps, Recent activity and the approvals that have a line (`integrations/audit.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. Neither module has a boundary mutant. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`. `test_uninstall_script.py` also imports the integration audit, but it reads `app/`, so it was not given to the tool. A mutant whose run times out counts as killed; none timed out.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/evidence.py` | test_excellence, test_excellence_catalog (+ test_excellence_evidence_mutation) | 53 of 53 (53 + 0) | 49 / 4 | **92.5%** | 49/53 → 53/53 | none | 53 / 0 | **100%** | none |
| `integrations/audit.py` | test_integrations_audit, test_integrations_app, test_integrations_extensions, test_sqlite_connections_closed (+ test_integrations_audit_mutation) | 42 of 42 (42 + 0) | 33 / 9 | **78.6%** | 33/42 → 42/42 | none | 42 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/evidence.py**:
  - Canonical text keeps non-ASCII characters as they are ("Café", not `é`).
  - A subject needs an id that is non-empty text.
  - An item subject needs both its workspace and its type.
  - A record's id is "ev-" and 16 hex digits.
  - A bundle lists the ids it kept, once each.
  - → `test_excellence_evidence_mutation.py`.
- **integrations/audit.py**:
  - A call's seconds are rounded to three places, and an error message is cut to 500 characters.
  - The store keeps 50,000 rows.
  - Recent activity returns the newest lines first, 100 by default, and asks for at least 1 and at most 1,000.
  - No approvals asked is an empty set; otherwise only the approvals with a line, started or outcome, are named.
  - → `test_integrations_audit_mutation.py`.

### Left

None.

# Mutation testing, batch 119

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- A notebook cell's change to a Prod workspace waiting for its typed name: the workspaces marked Prod and remembered on this PC, the refusal, the typed name and the allowance it gives (`prod_writes_service.py`).
- Secrets in files about to be committed: how much of a finding is shown, how much of a file and a line is read, binary files and placeholders (`git/secrets.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_redactor_characterization.py::test_the_audit_page_shows_this_table` reads `docs/`, so it was left out with `--deselect`. A mutant whose run times out counts as killed; none timed out.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `prod_writes_service.py` | test_prod_writes (+ test_prod_writes_mutation) | 76 of 76 (72 + 4) | 61 / 15 | **80.3%** | 61/72 → 72/72 | 0/4 → 4/4 | 76 / 0 | **100%** | none |
| `git/secrets.py` | test_git, test_git_secrets_edges, test_git_secrets_shapes, test_redactor_characterization (+ test_git_secrets_mutation) | 63 of 63 (60 + 3) | 53 / 10 | **84.1%** | 53/60 → 60/60 | 0/3 → 2/3 | 62 / 1 | **98.4%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **prod_writes_service.py**:
  - The typed name allows the next run for 120 s, a refusal can be confirmed for 600 s, and 500 Prod workspaces are remembered. The earlier tests used the names of these limits, so a changed number passed them.
  - An allowance still holds at exactly 120 s and a refusal can still be confirmed at exactly 600 s; half a second later neither can.
  - A project's keys workspace is marked Prod too.
  - New ids are added after the ones remembered, and only the newest 500 are kept.
  - Ids already remembered aren't written again.
  - Only non-empty text ids in the file count.
  - "Not marked" is `False`, with no id, no project, a project that can't be read, or environments that can't be read; the last remembers nothing.
  - → `test_prod_writes_mutation.py`.
- **git/secrets.py**:
  - A finding shows four characters of a secret longer than 12, and one of a secret of 12 or fewer.
  - A file is read up to its 2,000,000th byte, and a line up to its 20,000th character.
  - A NUL in the first 8,000 bytes makes a file binary; one just after doesn't.
  - A value made of every one of `x`, `*` and `.` is a placeholder.
  - → `test_git_secrets_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `git/secrets.py` | 78 | `len(line) > 20000` to `>=` | Equivalent: a line of exactly 20,000 characters cut to `line[:20000]` is the same line. |

# Mutation testing, batch 120

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- What the headless agent runs: an environment's settings, the settings file, where pipelines leave run requests, a pipeline run looked up in Fabric, and what a run hands the engine (`runtime.py`).
- Shell commands from notebook cells: the shell a script cell uses, the cmd script file, running a command and reading its output, and ending a command on Windows (`shell.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. Neither module has a boundary mutant. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

A mutant whose run times out counts as killed. Four of `shell.py`'s timed out: they flip the checks that the reader thread has the output pipe and the input thread has the input pipe, or that input was given, so a thread fails or never starts and the command or the read waits forever.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways. The Windows parts of `shell.py` are tested through its module-local `os` (`SimpleNamespace(name="nt")`) and a stand-in for `taskkill`, so they run on every OS.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `runtime.py` | test_runtime_edges, test_agent, test_cli_local_commands, test_json_files (+ test_runtime_mutation) | 44 of 44 (44 + 0) | 33 / 11 | **75.0%** | 33/44 → 44/44 | none | 44 / 0 | **100%** | none |
| `shell.py` | test_shell_and_decimals_edges, test_notebook_magics, test_app_control, test_runtimes_edges, test_subprocess_timeouts (+ test_shell_mutation) | 85 of 85 (85 + 0) | 75 / 10 (4 timeouts) | **88.2%** | 75/85 → 84/85 | none | 84 / 1 | **98.8%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **runtime.py**:
  - An environment's settings are a copy of the project's, and an environment it doesn't have is `{}`.
  - The settings file is indented by two spaces and leaves out the project and the alert address, which come from `agent.toml`.
  - Run requests go to the environment's LH_Meta in OneLake, and to its folder (no LH_Meta id) on this PC, and the backend is returned.
  - A pipeline run can't be looked up for an environment that runs on this PC.
  - The entity names are the project's.
  - One entity runs on its own unless a cascade is asked; the attempt is 1 when the job doesn't say; names and a backfill reach the engine as given.
  - The keys store is opened only when the run uses keys, and is handed to the engine.
  - → `test_runtime_mutation.py`.
- **shell.py**:
  - A `%%sh` cell uses sh, then bash, then nothing else; a `%%bash` cell uses bash.
  - A cmd script file that is already gone is no error.
  - A command given as a list runs with its arguments, not through the shell.
  - The threads that read a command's output and feed its input are daemons.
  - On Windows, `taskkill /F /T` ends the command's tree, with its output captured, no check and a 30 s limit, and the command is then awaited for 5 s.
  - → `test_shell_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `shell.py` | 105 | `text=True` to `False` | Equivalent: `encoding="utf-8"` is also given, and `subprocess.Popen` opens text pipes when an encoding is given. |

# Mutation testing, batch 121

One module not covered before, touched by no open pull request, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- Data mesh > Home: the incidents that reach data products, the team lead's products and what they depend on, on call, and the organization's counts per domain (`central/home.py`).

`central/keys_home.py` was planned too. Its "before" run finished (77 of 77 mutants, 48 killed and 29 survived, **62.3%**, with test_shared_keys and test_platform_service_paths), but its kill tests weren't written before this lane's stop, so it is left for a later batch. Most of its survivors are in `setup`, the steps that create the Keys workspace, which no test runs step by step.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. `central/home.py` has 142 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: test_mesh_home doesn't read `docs/`, `examples/` or `app/`. A mutant whose run times out counts as killed; none timed out.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/home.py` | test_mesh_home (+ test_mesh_home_mutation) | 142 of 142 (139 + 3) | 115 / 27 | **81.0%** | 113/139 → 136/139 | 2/3 → 3/3 | 139 / 3 | **97.9%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/home.py**:
  - An incident without a title is named by its name, and a missing priority, owner, opening time or number is empty text.
  - Open incidents come first, then by priority, with no priority last.
  - A product of mine without a name or version shows its id and no version; a deprecated one without a date "stops soon".
  - What I depend on is what my project reads or I follow, never my own project's products or a product the catalog no longer has. A dependency without a version is "Version 1".
  - Only my ten newest asks are shown.
  - Owner gaps count only the products with no owner.
  - A request is stale after more than 5 days (5 days is not stale), and the five oldest are shown, oldest first.
  - Domains sort by name (by id when they have none). A domain counts each product with an incident, and adds no "without a second owner" line when every product has two owners.
  - → `test_mesh_home_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/home.py` | 139 | `p.get("domain_id") or ""` to `and`, as the fallback of `parent.get` | Equivalent: the fallback is used only for a domain id that isn't a domain, and no domain row has that id or `""`, so the product counts toward no domain either way. |
| `central/home.py` | 143 | `(_days(...) or 0) > STALE_DAYS` to `or 1` | Equivalent: the fallback is used for a time that can't be read or 0 days, and 0 and 1 are both 5 or less, so the request isn't stale either way. |
| `central/home.py` | 143 | `-(r["days"] or 0)` to `or 1` in the sort | Equivalent: only requests with more than 5 days are in the list, so `days` is never 0 or None there. |

# Mutation testing, batch 122

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Integration settings, connected tools and approvals in their SQLite store: settings, tools and their tokens, requests, answers and uses (`integrations/store.py`).
- Learn: a module and its lab, a check recorded in Profile, hints, decision patterns, dismissing a finding and Try the fix (`learn_service.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_sqlite_durability.py` names `docs/` only in its docstring, so it runs in the copy. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` reads `docs/`, so it was left out with `--deselect`. `test_decision_patterns.py` was left out as a whole: three more of its tests fail in a copy of `sidecar/` on its own, since they read the patterns page in `docs/`. A mutant whose run times out counts as killed; none timed out.

The store is used by almost every integrations test. The run used the nine files that work with the store most directly. test_integrations_executions (about 40 s), and the MCP, HTTP and conformance tests, which start servers, were left out to keep each mutant's run short. So the "before" figure is for those nine files.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/store.py` | test_integrations_permissions, test_sqlite_stores, test_store_upgrades, test_integrations_audit, test_integrations_workspace_scope, test_local_retention_gaps, test_sqlite_durability, test_store_crash_kills, test_integrations_peer (+ test_integrations_store_mutation) | 112 of 112 (111 + 1) | 85 / 27 | **75.9%** | 85/111 → 111/111 | 0/1 → 1/1 | 112 / 0 | **100%** | none |
| `learn_service.py` | test_learn_service, test_learn_fixes, test_learn_fix_checked, test_learn_team, test_learn_guides (+ test_learn_service_mutation) | 59 of 59 (59 + 0) | 46 / 13 | **78.0%** | 46/59 → 59/59 | none | 59 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/store.py**:
  - A request waits 300 s, an approval must be used within 600 s, arguments are shown up to 20,000 characters, uses are kept 90 days, and the default HTTP port is 47615.
  - Changing a setting returns every setting, and an unknown setting is refused and not saved.
  - Changing a tool returns it, with its name stored as given, its grants and auto lists sorted and without repeats, and its workspaces in the order given. A change to any other field, or to a tool that isn't there, is refused.
  - A new token is returned and works; a tool that is gone or disconnected gets none, and its old token stops working.
  - The same call has the same hash whatever the order of its arguments.
  - Ids have ten hex digits, and tokens 43 URL-safe characters after `fdt_`.
  - Disconnecting a tool, or answering a request, that isn't there is refused.
  - The request list shows 50 by default, and the list of uses 200.
  - Arguments of exactly 20,000 characters are shown as they are; longer ones are cut to 20,000.
  - → `test_integrations_store_mutation.py`.
- **learn_service.py**:
  - A module shows each exercise's number of hints, and its size ("S" when none is given) and datasets (none when none are given).
  - Saving an answer says it was saved.
  - Only a passed check is recorded in Profile, and what it earned is returned.
  - The first hint is level 1.
  - The decision patterns are the shipped ones.
  - A rule whose id starts with "NB" is checked against the notebook policy, any other against the SQL one, and where and the line reach the dismissal as given.
  - Try the fix hands the findings on (none when none are given) and returns the proposal.
  - → `test_learn_service_mutation.py`.

### Left

None.

# Mutation testing, batch 124

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Ranks earned from Learn evidence: the ladder's requirements, what the evidence counts, the top rank and when a rank can be earned today (`ranks.py`).
- Workspace monitoring views for a warehouse: the row cap, Kusto's reply and errors, and the KQL queryset the app keeps in Fabric (`warehouse_monitoring.py`).

`integrations/permissions.py` was planned for this batch but left out, because it is on the approval and policy skip list; `warehouse_monitoring.py` took its place.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ranks.py` | test_ranks, test_profile_goals_impact (+ test_ranks_mutation) | 107 of 107 (102 + 5) | 80 / 27 | **74.8%** | 78/102 → 102/102 | 2/5 → 5/5 | 107 / 0 | **100%** | none |
| `warehouse_monitoring.py` | test_warehouse_monitoring, test_warehouse_monitoring_build_again, test_warehouse_monitoring_service_mutation (+ test_warehouse_monitoring_mutation) | 113 of 113 (111 + 2) | 97 / 16 | **85.8%** | 96/111 → 111/111 | 1/2 → 2/2 | 113 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ranks.py**:
  - The ladder's numbers: 3 paths for Master Forger, 25 exercises and 4 labs for Pack Runner, 3 fixes, 5 paths, 8 labs and 10 fixes for Pack Leader, 15 notebooks and 90 days. The ladder version is 1.
  - The top rank has no next one, and the rank below it names the top rank as next.
  - Exercises, fixes and notebooks are each counted once, and only from their own kind of evidence.
  - The days count spans the first and last dated evidence; a time it can't read is left out, and no dated evidence is 0 days.
  - "Finish 1 learning path" and "Finish 2 learning paths". A single lesson is named on its own. An unknown requirement is an error.
  - Labs and exercises can be earned once exactly enough have shipped, and only labs count as shipped labs.
  - → `test_ranks_mutation.py`.
- **warehouse_monitoring.py**:
  - At most 500 rows, Kusto is asked for 501, and exactly 500 rows are not truncated. Each try waits up to 90 s.
  - An empty reply is no columns, no rows and not truncated.
  - A column's type is its `DataType` or else its `ColumnType`, and a table without columns is read.
  - A 403 adds Kusto's message only when there is one.
  - A long warehouse name is cut to 200 characters in the queryset's name.
  - A queryset needs a database. Its definition is JSON indented by two.
  - Reading a queryset keeps its version and other keys, uses 1.0.0 when there is no version, and reads a definition without the queryset part as an empty one.
  - → `test_warehouse_monitoring_mutation.py`.

### Left

None.

# Mutation testing, batch 125

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds (neither module has a boundary mutant):
- Creating the platform in Fabric, or joining one: each step's detail, workspaces found or made, and the variable library (`central/setup.py`).
- The best practices check of a semantic model, run in Fabric: which runs are its checks, the risk check, findings kept on this PC, the request id and the check notebook (`model_bpa_service.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`. Of `test_platform.py`, which takes about 30 s, only `test_platform_setup_makes_every_item_fabric_accepts` calls `central/setup.py`, so only that test was given, by its node id.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0.

One mutant of `model_bpa_service.py` timed out and counts as killed: with `_STARTING.acquire(blocking=True)`, a second check starting at the same time waits for ever instead of being refused.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/setup.py` | test_platform_setup_steps, test_landing_zones, test_platform_service_paths, test_platformtest, one test of test_platform (+ test_central_setup_mutation) | 91 of 91 (91 + 0) | 83 / 8 | **91.2%** | 83/91 → 90/91 | none | 90 / 1 | **98.9%** | 1 equivalent |
| `model_bpa_service.py` | test_model_bpa (+ test_model_bpa_service_mutation) | 103 of 103 (103 + 0) | 85 / 18 | **82.5%** | 85/103 → 103/103 | none | 103 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/setup.py**:
  - The LH_Landing step says "Created LH_Landing" on a first run and "Kept LH_Landing" on the next.
  - A new workspace that Fabric answers without its name takes the name asked for, in both setups.
  - The variable library's files are JSON indented by two.
  - → `test_central_setup_mutation.py`.
- **model_bpa_service.py**:
  - A model's checks are only runs of this kind for this model, read from the last 200 runs without a refresh; a running one is asked of Fabric by its job.
  - The risk check is refreshed only when asked; otherwise the kept one is read, or an unknown one.
  - An environment without LH_Meta isn't set up.
  - A missing model's message names the workspace, or says "the workspace".
  - Findings kept on this PC aren't read from OneLake again.
  - A request id is the time and four hex digits.
  - A 404, or an `ItemNotFound` or `EntityNotFound` code on its own, publishes the notebook again.
  - An acknowledgement names the workload, or else the model, and is stamped in UTC to the second.
  - The notebook's id is Fabric's answer, or else the one found by name; with neither it is an error.
  - → `test_model_bpa_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/setup.py` | 68 | a failed step's `return False` dropped to `None` | Equivalent: `step` is only read as true or false (`if not step(...)` and `step(...) and step(...)`), and `None` is false like `False`. |

# Mutation testing, batch 126

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Learn content: listing modules and labs, and the checks that say what is wrong with the content before it ships (`learn/content.py`).
- Entra ID groups per domain: the plan, the scripts shown and saved, linking a group by object id, and the directory reads (`central/entra_service.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_learn_content.py::test_docs_pages_a_module_names_exist` reads `docs/`, so it was left out with `--deselect`. Of `test_regex_anchors.py`, which scans the whole engine, only `test_academy_and_learn_ids_ending_in_a_line_break_are_refused` calls `learn/content.py`, and of `test_estate.py` only `test_a_graph_error_is_one_short_line_without_a_token` calls `central/entra_service.py`, so only those tests were given, by their node ids.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `learn/content.py` | test_learn_content, test_learn_content_edges, test_learn_labs, test_profile_goals_impact, test_profile_learn, test_ranks, one test of test_regex_anchors (+ test_learn_content_mutation) | 114 of 114 (111 + 3) | 105 / 9 | **92.1%** | 104/111 → 111/111 | 1/3 → 3/3 | 114 / 0 | **100%** | none |
| `central/entra_service.py` | test_entra_graph, test_entra_groups, one test of test_estate (+ test_entra_service_mutation) | 121 of 121 (120 + 1) | 104 / 17 | **86.0%** | 104/120 → 118/120 | 0/1 → 1/1 | 119 / 2 | **98.3%** | 2 not killed on Linux |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **learn/content.py**:
  - Drafts are listed unless left out, and a folder not named like a module isn't listed.
  - Raw HTML is reported on its own line number, counted from 1.
  - Minutes run from 1 to 120: 0 and 121 are refused, 1 and 120 are not.
  - A `faster_than` check without a baseline says it needs one, and names no file.
  - An exercise without an id is named with a question mark.
  - → `test_learn_content_mutation.py`.
- **central/entra_service.py**:
  - An unknown domain is refused before the catalog is asked.
  - Subdomains are left out of the groups and the saved script unless asked for.
  - A script shown is never saved.
  - CSV and PowerShell files carry a byte order mark, and a shell script doesn't.
  - Protected cells come with a note, and no note comes without them.
  - Linking refuses a group id that isn't an object id, before the catalog is called.
  - Workspaces are sorted by name.
  - Two characters are enough to search, and each Graph read gets 30 s.
  - Graph's error keeps 200 characters of its text.
  - Every suggested name is checked, fifteen at a time.
  - → `test_entra_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/entra_service.py` | 124 | `newline="\n" if ext == "sh" else None`: the test negated, and `==` to `!=` | Not killed on Linux: there `newline=None` also writes `\n`, so the file is the same. On Windows the files differ, and `test_csv_and_powershell_files_carry_a_byte_order_mark_and_shell_scripts_unix_line_ends` checks each file's line ends there. |

# Mutation testing, batch 127

One module not covered before, not touched by an open pull request, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- App Studio generation: the plan schema's limits, what a checked plan keeps and cuts, and the layout of the specification made from it (`appstudio/generate.py`).

`proof.py` was planned for this batch but left out because the lane reached its time limit before it could run; it still has no score.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so the run below can't score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/generate.py` | test_appstudio_generate, test_appstudio_generate_edges, test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_publish, test_appstudio_publish_edges, test_appstudio_service_edges (+ test_appstudio_generate_mutation) | 142 of 142 (141 + 1) | 102 / 40 | **71.8%** | 101/141 → 140/141 | 1/1 → 1/1 | 141 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **appstudio/generate.py**:
  - The plan schema's limits: 10 pages, 12 components a page, titles of 120 and 200 characters, a purpose of 500, at most 5 audiences of 120 and 5 questions of 300, and no other properties.
  - The instruction names the limits and the target's component types.
  - The grid sizes, and a fifth card wraps to a new row.
  - A measure without a formula is referenced by name.
  - A plan keeps at most 10 pages of 12 components, and an untitled page is "Page 1", "Page 2" and so on.
  - The plan's name, purpose, audiences, questions and titles are cut to their limits.
  - What is left out is named within its limits (a type of 40 characters, a measure of 80, a field of 120).
  - Nothing usable lists at most twenty reasons.
  - A table without columns, and metadata without relationships, have none in the context.
  - Only a text component carries its title as text.
  - The sample metadata lists each column once and isn't truncated.
  - → `test_appstudio_generate_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `appstudio/generate.py` | 196 | a wrapped row's height starts at 1 instead of 0 | Equivalent: the row's height is then `max(row_h, h)`, and every component is at least 1 high (`_SIZE` and `_DEFAULT_SIZE`), so it is `h` either way. |

# Mutation testing, batch 128

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- App Studio conversational edits: the change schema's limits, how each named change becomes patch operations, and what is left out and why (`appstudio/edit.py`).
- The agent's own credentials: the service principal's key file, a refusal Entra gives for good, and reading a certificate (`credentials.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was used. Of `test_corporate_network.py`, whose other tests read `app/`, only `test_sign_in_calls_have_a_timeout` and `test_managed_identity_uses_a_session_with_a_timeout` call `credentials.py`, so only those were given, by their node ids. `test_tsql_contract.py` (a live run, skipped without a tenant) and `test_cli_tenant_commands.py` (which replaces `credentials.from_config`) were left out.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0.

One mutant of `appstudio/edit.py` timed out and counts as killed: with `n += 1` changed to `n += 0`, a new id that is taken is looked for for ever.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/edit.py` | test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_service_edges (+ test_appstudio_edit_mutation) | 136 of 136 (136 + 0) | 96 / 40 | **70.6%** | 96/136 → 136/136 | none | 136 / 0 | **100%** | none |
| `credentials.py` | test_agent, test_agent_edges, test_credential_expiry, test_credentials_config, test_run_as, test_secrets_at_rest, two tests of test_corporate_network (+ test_credentials_mutation) | 82 of 82 (81 + 1) | 76 / 6 | **92.7%** | 75/81 → 81/81 | 1/1 → 1/1 | 82 / 0 | **100%** | none |

### Bugs found

- **appstudio/edit.py**: two components added to the same page in one change overlap. `add_component` places a new component under the page's components as the specification had them (lines 192 and 193, which read `spec["pages"]`), not under the ones added earlier in the same change, so the second goes on the same row as the first. The preview accepts it. `test_an_added_component_goes_under_its_own_pages_components` doesn't pin the second one's row, so a fix won't break it. Not fixed here.

### What the survivors were, and the tests that now kill them

- **appstudio/edit.py**:
  - The change schema's limits: 30 changes, the widths, a summary of 300, titles of 200, at most 3 questions of 300, and no other properties.
  - The instruction names the widths and the target's component types.
  - A component without measures or fields is shown to the model with none.
  - Only 30 changes are read.
  - An added component goes under its own page's components, at the next index, with its field, and a text component takes its type as title and text.
  - A component added to a new page starts at the top, at index 0.
  - Titles and names are cut to their limits (200 for components, 120 for pages and the app), and a measure name is read to 128 characters.
  - What is left out is named within its limits, and nothing usable lists at most ten reasons.
  - The summary and questions are cut and kept; without a summary it is what was done, cut to 300.
  - Metadata without tables, or a table without measures, still allows a change.
  - → `test_appstudio_edit_mutation.py`.
- **credentials.py**:
  - Whether the key file is open answers `False`, never `None`: with no key file, for another kind of identity, a file that can't be read, and on Windows.
  - Only "others may read" makes it open: 644 is, 640 and 641 aren't.
  - A refusal says the agent waits 5 minutes before asking again; another error is no refusal.
  - A PFX without its certificate is refused in words.
  - → `test_credentials_mutation.py`.

### Left

None.

# Mutation testing, batch 130

One engine module not scored before, run with `--kinds all`, so the score counts the boundary mutants together with the earlier kinds:
- The approval that lets remote compute start: a confirmation in the app or the profile's execution policy, its expiry, and the check before a session opens (`engines/authorization.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has 39 mutants, so all of them ran. No `-k` filter or `--deselect` was needed.

The first run used test_engines, test_engines_service and test_engine_switch_failures. Each survivor was run again with test_databricks_sql, test_dbconnect and test_fabric_spark added, and the "before" figures are from that run. No mutant's run timed out.

Before each "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/authorization.py` | test_engines, test_engines_service, test_engine_switch_failures, test_databricks_sql, test_dbconnect, test_fabric_spark (+ test_engine_authorization_mutation) | 39 of 39 (38 + 1) | 35 / 4 | **89.7%** | 35/38 → 38/38 | 0/1 → 1/1 | 39 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/authorization.py**:
  - An approval from the profile's policy lasts the confirmation time from now, as a confirmation in the app does.
  - An approval is good just before its expiry and has run out at exactly its expiry.
  - The check hands back the approval it checked.
  - An approval can't be changed once given.
  - → `test_engine_authorization_mutation.py`.

### Left

None.

# Mutation testing, batch 131

One engine module not scored before, run with `--kinds all`:
- Access tokens for an Azure Databricks connection: CLI profiles, the Databricks CLI's own token, OAuth machine-to-machine, secret references, Windows Credential Manager, the refresh before expiry and the memory of refused tokens (`engines/databricks_auth.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 130. The module has 190 mutants, so 150 were sampled. No `-k` filter or `--deselect` was needed.

The first run used test_databricks_auth_errors, test_databricks_auth_replies, test_databricks_credman, test_databricks_host, test_databricks_profile_host and test_credential_expiry. Each survivor was run again with test_databricks_sql, test_credman_dpapi_calls, test_databricks_discovery, test_databricks_jobs, test_dbconnect_env and test_engines_service_edges added, and the "before" figures are from that run. No mutant's run timed out. The new tests passed in a copy of `sidecar/` on its own, against `ast.unparse` of the module, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/databricks_auth.py` | test_databricks_auth_errors, test_databricks_auth_replies, test_databricks_credman, test_databricks_host, test_databricks_profile_host, test_credential_expiry, test_databricks_sql, test_credman_dpapi_calls, test_databricks_discovery, test_databricks_jobs, test_dbconnect_env, test_engines_service_edges (+ test_databricks_auth_mutation) | 150 of 190 (147 + 3) | 136 / 14 | **90.7%** | 134/147 → 147/147 | 2/3 → 3/3 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/databricks_auth.py**:
  - The limits are the documented ones: a minute for the CLI, a refresh 120 s before expiry, 256 refused tokens remembered, Windows' 2,560-byte credential limit and 300 characters of the CLI's reason.
  - A token is fetched again from exactly 120 s before it expires, and not a moment earlier.
  - The refused-token memory keeps exactly the newest 256 and forgets the oldest first.
  - The CLI is started with its output captured as UTF-8 text, a 60 s timeout and no stdin.
  - advapi32 is opened with its last error kept.
  - A long reason from the CLI is cut to 299 characters and an ellipsis; one of exactly 300 is kept whole.
  - The token endpoint is asked with a 30 s timeout, and a lifetime under one second is still a lifetime.
  - Without an HTTP session given, the app's own session is used.
  - A CLI profile with a client ID but no secret, or a secret but no client ID, signs in with the CLI.
  - → `test_databricks_auth_mutation.py`, which also pins, for the first run's survivors that the wider run killed, an `env:` reference read from the given environment or this PC's, a missing CLI profile, a missing or hung CLI, a refused service principal, a connection holding a secret value, and the providers for a profile's service principal and for OAuth machine-to-machine.

### Left

None.

# Mutation testing, batch 132

One engine module not scored before, run with `--kinds all`:
- Databricks Connect: the runtime lines and their Python, the worker's environment and sign-in, gRPC's root certificates, frame output and what a session refuses (`engines/dbconnect.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 130. The module has 113 mutants, so all of them ran. No `-k` filter or `--deselect` was needed.

The first run used test_dbconnect, test_dbconnect_env and test_databricks_profile_host. Each survivor was run again with test_tls_inspection, test_engine_conformance and test_engines added, and the "before" figures are from that run. No mutant's run timed out. The new tests passed in a copy of `sidecar/` on its own, against `ast.unparse` of the module, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/dbconnect.py` | test_dbconnect, test_dbconnect_env, test_databricks_profile_host, test_tls_inspection, test_engine_conformance, test_engines (+ test_dbconnect_mutation) | 113 of 113 (112 + 1) | 106 / 7 | **93.8%** | 106/112 → 111/112 | 0/1 → 0/1 | 111 / 2 | **98.2%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/dbconnect.py**:
  - Connecting waits up to 15 minutes for a terminated cluster to start.
  - Runtimes 13 and 14 say they need Python 3.10, and 15 says 3.11.
  - Runtime 19 is known, and 20 is newer than this version knows.
  - → `test_dbconnect_mutation.py`, which also pins, for the first run's survivors that the wider run killed, gRPC's root certificates (written only when a folder is given, nothing is set already and Windows has roots), what a session refuses with the descriptor's own notes, and the adapter's descriptor.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/dbconnect.py` | 112 | `major > max(RUNTIME_PYTHON)` to `>=` | Equivalent: the branch is reached only when the runtime isn't in `RUNTIME_PYTHON`, so `major` is never equal to its largest key. |
| `engines/dbconnect.py` | 223 | `zip(strict=True)` to `False` | Equivalent: the worker builds each row from the same frame as its columns, so they always have the same length. |

# Mutation testing, batch 133

One engine module not scored before, run with `--kinds all`:
- The process a separate-runtime engine runs cells in: the protocol's lines on Windows and elsewhere, the session's start and its secrets, a cell's outputs and errors, interrupting, and the main loop (`engines/runtime_worker.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 130. The module has 59 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. The worker runs as a process of its own in the main tests, from the temporary copy, so those runs used the mutant too.

The first run used test_runtimes and test_tls_inspection. Each survivor was run again with test_dbconnect added, and the "before" figures are from that run. Two mutants of the main loop's `exec` branch (line 206) timed out in the first run and count as killed. The new tests run the worker in the test's own process against fakes. They passed in a copy of `sidecar/` on its own, against `ast.unparse` of the module, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/runtime_worker.py` | test_runtimes, test_tls_inspection, test_dbconnect (+ test_runtime_worker_mutation) | 59 of 59 (58 + 1) | 40 / 19 | **67.8%** | 40/58 → 57/58 | 0/1 → 1/1 | 58 / 1 | **98.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/runtime_worker.py**:
  - On Windows, the protocol's lines come from what PeekNamedPipe says has arrived: it waits 0.02 s when nothing has, joins a line sent in two parts, reads bytes that aren't UTF-8 as a replacement character, ends when the engine closes its end, and opens kernel32 with its last error kept. A peek reads nothing itself.
  - A version that can't be read is empty.
  - A Databricks Connect session's client is the installed distribution's version, or the module's own when that is empty.
  - A new worker isn't running, and a cell's end always sets it back to not running.
  - An empty cell runs and shows nothing.
  - Interrupt reaches the session only while a cell runs, and a failure to stop is not the cell's error.
  - Windows trust that fails keeps Python's own list.
  - The main loop puts the runtime's folder first, skips a line that isn't JSON, starts each cell in a daemon thread with 100 preview rows unless told otherwise, passes on interrupt, stops at quit, reports a start that fails, and runs Databricks Connect when no kind is named.
  - → `test_runtime_worker_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/runtime_worker.py` | 34 | `if TYPE_CHECKING:` negated | Equivalent: it only imports `collections.abc` names used in annotations, which `from __future__ import annotations` never evaluates. |

# Mutation testing, batch 134

Two small workload modules not covered before, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Reading the status records agents publish, when an agent is live, offline or stopped, comparing its version with this app's, and the fleet list built from every environment's records (`workloads/agents.py`).
- The likely category of a failed run's error message, and whether retrying alone helps (`workloads/classify.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before each "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/agents.py` | test_agent_fleet, test_incidents (+ test_workload_agents_mutation) | 45 of 45 (42 + 3) | 40 / 5 | **88.9%** | 40/42 → 42/42 | 0/3 → 2/3 | 44 / 1 | **97.8%** | 1 equivalent |
| `workloads/classify.py` | test_incidents, test_retrying (+ test_workload_classify_mutation) | 16 of 16 (16 + 0) | 12 / 4 | **75.0%** | 12/16 → 15/16 | none | 15 / 1 | **93.8%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/agents.py**:
  - Only status records that are JSON objects of kind `agent_status` are read; another kind, a list, text that isn't JSON and a missing file are skipped.
  - An agent is offline from exactly its stale time, and live one second before.
  - An older heartbeat read later doesn't speak for the agent, and of two equal heartbeats the first record read does.
  - → `test_workload_agents_mutation.py`.
- **workloads/classify.py**:
  - Each category says whether retrying alone helps: throttled, network and table busy do; access, source missing, schema, data quality and code don't; an unrecognized message doesn't say.
  - → `test_workload_classify_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/agents.py` | 56 | `a < b` to `<=` for older or newer | Equivalent: equal versions are answered as `same` just before, so `a` and `b` are never equal here. |
| `workloads/classify.py` | 16 | `429` to `430` in the statuses left out of the network rule | Equivalent: 429 is matched by the throttled rule, which comes first, so the network rule never decides it. |

# Mutation testing, batch 135

One workload module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- How a notice route sends: checking a Teams, flow or email address, the company mail server's settings, the email itself, sending it encrypted, and posting to a Power Automate flow (`workloads/deliver.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/deliver.py` | test_deliver, test_incidents (+ test_deliver_mutation) | 131 of 131 (125 + 6) | 105 / 26 | **80.2%** | 105/125 → 124/125 | 0/6 → 6/6 | 130 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/deliver.py**:
  - A line is cut to 500 characters, a route sends at most 200 lines, and an email route at most 20 addresses, with exactly 20 allowed.
  - An empty address stays empty.
  - A webhook with only a user name, or only a password, is refused.
  - An address of exactly 254 characters is kept and one of 255 refused; a bad address is named in at most 80 characters.
  - The mail server's port is 587 by default, 1 and 65535 are allowed and 65536 isn't, and its settings can't be changed in place. Its user name is cut to 256 characters.
  - An email's subject is cut to 200 characters, its Message-ID is on the sender's domain, and its date is in local time. It shows 20 lines and says how many more.
  - The mail server gets 20 seconds by default, and a flow 5 seconds to connect and 15 to answer. A flow answering 300 has failed.
  - A server reply given as text, not bytes, still gives its status code.
  - → `test_deliver_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/deliver.py` | 124 | `1 <= port` to `0 <= port` | Equivalent: a port of 0 is read as the default 587 just before, and the port is a whole number, so no port is 0 here. |

# Mutation testing, batch 136

One workload module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- What an evaluation looks for in one environment: failed runs, runs waiting for a decision, lateness, data product promises, success objectives and agents that stopped reporting, and the one line each change becomes in a Teams message (`workloads/evaluate.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/evaluate.py` | test_incidents, test_incident_desk, test_agent_clock_and_folder, test_not_set_up (+ test_evaluate_mutation) | 116 of 116 (113 + 3) | 89 / 27 | **76.7%** | 88/113 → 113/113 | 1/3 → 3/3 | 116 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/evaluate.py**:
  - A failed run with no message says "The run failed.", and one with no place or job gives empty ones. A run waiting for a decision with no message says what waits.
  - A load of exactly one row counts its rejected rows, a load with no rejected count rejected nothing, and exactly the promised share of rejected rows keeps the promise.
  - A product's lateness counts unless the caller says the entity reports it already.
  - The products a late table lets down go on its late signal, not on a failed-run signal before it.
  - An objective names the newest finished run, not one still going.
  - An agent is missing from exactly its stale time.
  - Times in messages are shown in UTC; a naive time and text that isn't a time are shown as they are.
  - A recovery says recovered or cleared, and names the environment when the incident has no name. An escalation names who, else on-call. A reopened incident says "again" and names its owner.
  - → `test_evaluate_mutation.py`.

### Left

None.

# Mutation testing, batch 137

One workload module not covered before, run with `--kinds all` (it has no boundary mutants):
- A workload's health, readiness and findings, each with its evidence (`workloads/health.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/health.py` | test_workload_health, test_workloads (+ test_workload_health_mutation) | 91 of 91 (91 + 0) | 84 / 7 | **92.3%** | 84/91 → 91/91 | none | 91 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/health.py**:
  - A workload is partly observed when one deployment was read and another wasn't; not when all or none were, and not when the unread one has a last run.
  - A definition problem alone doesn't make a workload that runs well need attention; it only blocks readiness.
  - A run waiting for a decision has a default text, and a cancelled run has an empty one unless it has a message.
  - An older version is named by eight characters of each hash.
  - → `test_workload_health_mutation.py`.

### Left

None.

# Mutation testing, batch 138

Two small incident modules not covered before, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Who an incident reaches: the data products built on its workload, the projects linked to them and the people to tell (`workloads/impact.py`).
- SLA targets for incidents, from the policy or the defaults, and where an incident stands against them (`workloads/sla.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before each "after" run, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/impact.py` | test_incident_desk (+ test_incident_impact_mutation) | 36 of 36 (36 + 0) | 32 / 4 | **88.9%** | 32/36 → 36/36 | none | 36 / 0 | **100%** | none |
| `workloads/sla.py` | test_incident_desk (+ test_incident_sla_mutation) | 70 of 70 (65 + 5) | 53 / 17 | **75.7%** | 53/65 → 65/65 | 0/5 → 4/5 | 69 / 1 | **98.6%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/impact.py**:
  - A product without a name is named by its id, and one without an owner, domain or status gets empty ones; a link without an alias gets an empty one. A product and link that have them keep them.
  - → `test_incident_impact_mutation.py`.
- **workloads/sla.py**:
  - The default targets for P1 to P4, and the longest target, a year in minutes.
  - A target from 1 minute up to a year is taken; -1 and a minute over a year are ignored and reported.
  - An incident with no opening time has no clocks.
  - Acknowledged at exactly the due time is met, a second later missed.
  - At exactly the due time an open clock is at risk with 0 minutes left, a second later breached.
  - At risk from less than a quarter of the window left, and minutes left are whole minutes.
  - → `test_incident_sla_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/sla.py` | 51 | `0 < value` to `0 <= value` | Equivalent: a value of 0 is taken as "no target" just before, so it never reaches this check. |

# Mutation testing, batch 139

One incident module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- Incident history for the central lakehouse: timeline and snapshot rows, the record of what this PC already copied, and reports of time to acknowledge, time to resolve and SLAs met (`workloads/incident_history.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/incident_history.py` | test_incident_desk, test_corrupt_state_reads (+ test_incident_history_mutation) | 86 of 86 (83 + 3) | 68 / 18 | **79.1%** | 67/83 → 83/83 | 1/3 → 3/3 | 86 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/incident_history.py**:
  - This PC remembers 50,000 copied record ids, and reports cover 7, 30, 90 or 365 days.
  - A snapshot of an incident that leaves fields out gets the defaults ("detected" and empty texts), and its "as of" time is when it opened; one that has the fields keeps them, and its newest timeline entry is its "as of" time.
  - A timeline entry without text is copied with empty text.
  - A record of copies that isn't JSON, or a folder where the file should be, sends everything again.
  - A naive time is read as UTC.
  - A report counts incidents opened at exactly either end of its window, and all met is 100 %.
  - → `test_incident_history_mutation.py`.

### Left

None.

# Mutation testing, batch 140

One workload module not covered before, run with `--kinds all`:
- Turning each native source into workload records: runs from the run log, Fabric and schedules on this PC, owners, and the metadata entity, notebook, local pipeline and Fabric item workloads with their deployments, schedules and links (`workloads/inventory.py`).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has 260 mutants, so 150 were sampled; the sample held no boundary mutant. No `-k` filter was used. One test was left out with `--deselect` to keep each mutant's run short: `test_workloads.py::test_ten_thousand_workloads_filter_and_page_quickly`, which builds 10,000 Fabric item records with this module and then times filtering and paging them (`workloads/query.py`); it checks the speed of the paging, not the records, which the other tests check, and takes about 1 s a run. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

The first run used test_workloads only, and scored 61 / 89 (40.7%). Each survivor was run again with test_workload_pipelines, test_workload_service_paths, test_workload_audit and test_workload_query added, which reach this module through the workload service, and the "before" figures are from that run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/inventory.py` | test_workloads, test_workload_pipelines, test_workload_service_paths, test_workload_audit, test_workload_query (+ test_workload_inventory_mutation) | 150 of 260 (150 + 0) | 85 / 65 | **56.7%** | 85/150 → 150/150 | none sampled | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/inventory.py**:
  - A run by an agent names the agent, and its host when it has one. A run ends its seconds after it started (0 seconds too), and one without seconds or a start has no end.
  - A Fabric run started from this app, a Fabric job and a schedule run on this PC each get empty or "workspace" defaults for what they leave out, keep what they have, and a job takes either spelling of its times. Runs are newest first, one with no start last.
  - A project with no name is "project", and one with no team or contacts has no team or business contact; one that has them names its team and first contact.
  - Where tables go in each environment, which workspace and whose identity an environment that runs in Fabric shows, and that an environment not set up offers no run.
  - The newest deployment says which version an environment runs.
  - An environment's schedules on this PC are the ones for this entity or for all entities, on unless they say otherwise; Fabric schedules are off unless they say so.
  - The products an entity feeds use the product's name when it has no title. An entity's errors are its own, without its name. Its upstream tables are the ones it reads.
  - A kept link or a link from a run gets defaults for a missing workspace name and way of linking.
  - A linked notebook's Fabric runs are cached until signed in, it has only its own schedules, names its lakehouse's workspace, can be cancelled while a run is going, and doesn't list a schedule's last run twice. One linked by name shows how the Fabric items were read. Its last run here is its newest schedule run.
  - A pipeline run the app stopped says so, a message from the schedule wins, and its mocks are counted. A pipeline only a schedule runs is listed by its folder name; the first pipeline at a path is kept, and its place and problems are shown.
  - Runs count for a pipeline through its schedule, or by its name only when the name is unique; a run whose record is gone shows the schedule's own row.
  - A Fabric item gets defaults for a missing name, workspace name and description, its schedules are off unless they say so, it shows how it was read once it has runs, can be cancelled while one is going, and a Preview type can't be scheduled.
  - → `test_workload_inventory_mutation.py`.

### Left

None.

# Mutation testing, batch 141

One workload module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- Workload ids, run states, observations and how a read error is shown (`workloads/model.py`).

The run was at `main` 4ddd3662 (2026-11-18), with the same tool and method as batch 140. The module has fewer than 150 mutants, so all of them ran. `test_workloads.py::test_ten_thousand_workloads_filter_and_page_quickly` was left out with `--deselect`, as in batch 140, to keep each mutant's run short. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/model.py` | test_workloads, test_workload_query, test_not_set_up, test_typed_errors, test_retrying (+ test_workload_model_mutation) | 95 of 95 (94 + 1) | 84 / 11 | **88.4%** | 84/94 → 94/94 | 0/1 → 1/1 | 95 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/model.py**:
  - Every kind of id reads back, and an id missing a part (a project, entity, workspace, item, name or path) isn't one.
  - A time given as seconds is UTC, a naive time is read as UTC, and an aware one keeps its offset.
  - An unknown observation state is refused.
  - A cached read exactly its maximum age old is still cached.
  - A 401, 403 or 429 status is read from the error itself, not from its words.
  - → `test_workload_model_mutation.py`.

### Left

None.

# Mutation testing, batch 142

Teams messages for incidents (`workloads/notify.py`): routes, quiet hours, the outbox that keeps a message until Teams takes it, a failing route's rest between tries, and the audit line for a notice that wasn't delivered.

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. The module has 221 mutants, so 150 were sampled. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

The first run was stopped after 103 of its 150 mutants. The other 47 were run with `--only`, with the same seed and the same tests, so the "before" figures cover all 150. Before each "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, and passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/notify.py` | test_notify_delivery, test_incidents, test_notice_failures, test_platform_notify, test_agent_clock_jumps (+ test_notify_mutation) | 150 of 221 (144 + 6) | 125 / 25 | **83.3%** | 124/144 → 140/144 | 1/6 → 5/6 | 145 / 5 | **96.7%** | 4 equivalent, 1 noted under Left |

No bugs were found in the sampled lines that the tests now cover. One mutant that is left points at a small gap, described under Left.

### What the survivors were, and the tests that now kill them

- **workloads/notify.py**:
  - The outbox keeps 500 messages and sends at most 25 to a route in one call. The existing tests used the names, so a changed number still passed.
  - Each kept error (a message added, a post that failed, a failed try, a failing route) is cut to 500 characters.
  - A waiting message keeps the time it was made.
  - A failing route's address is kept as a 16-digit fingerprint, never as the webhook.
  - No routes send, queue and hold nothing.
  - An escalation goes to the route the incident names, and to the escalation route when it names none.
  - With no time given, quiet hours go by the outbox's clock.
  - The outbox's folder is made with its parents, and a route can't be changed once made.
  - A message due at exactly now goes now.
  - A route at its longest rest is still resting, up to exactly the clock margin.
  - Quiet hours start at exactly their start, by day and overnight.
  - An audit line for a result without a queued count says 0.
  - → `test_notify_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/notify.py` | 182, 184 | `lastrowid or 0` to `or 1` | Equivalent: an INSERT always gives a row id, so the fallback is never used. |
| `workloads/notify.py` | 236 | `min(failures - 1, 20)` to `21` | Equivalent: 60 s × 2^20 is far beyond `BACKOFF_MAX` either way, and the wait is capped at `BACKOFF_MAX`. |
| `workloads/notify.py` | 266 | `per_route.get(name, 0)` to `1` in the check | Equivalent: it only changes the first check for a route, where 1 is still below `MAX_PER_TRY` (25). |
| `workloads/notify.py` | 96 | `end > now` to `>=` | Reachable only when quiet hours end on a second other than :00. agent.toml refuses such a time, but platform notice settings accept one (`22:00-06:00:30`), and a message held then waits until 06:00 the next day instead of 06:00:30. Reported for a decision, not changed here (tests only). |

# Mutation testing, batch 143

Tables scheduled in two places (`workloads/overlap.py`): an agent and this PC, or two agents, running the same tables.

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 53 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/overlap.py` | test_schedule_overlap (+ test_schedule_overlap_mutation) | 53 of 53 (52 + 1) | 46 / 7 | **86.8%** | 46/52 → 51/52 | 0/1 → 1/1 | 52 / 1 | **98.1%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/overlap.py**:
  - A schedule that can't be described has empty text.
  - An agent's tables against every table on this PC are the agent's tables.
  - A schedule on this PC that doesn't say whether it is enabled counts, and keeps its text, or empty text when it has none.
  - A record without an agent name is left out.
  - Of two records with the same heartbeat, the later one speaks.
  - → `test_schedule_overlap_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/overlap.py` | 80 | `known[i + 1:]` to `known[i:]` | Equivalent: the extra pair is a schedule with itself, which is skipped as the same place. |

# Mutation testing, batch 144

Teams messages for platform events (`workloads/platform_notify.py`): checking an event, its message, routes, and the ledger that posts each change once.

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 167 mutants, so 150 were sampled. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/platform_notify.py` | test_platform_notify, test_notify_delivery, test_notice_failures, test_deliver, test_governance_notices (+ test_platform_notify_mutation) | 150 of 167 (150 + 0) | 132 / 18 | **88.0%** | 132/150 → 150/150 | none | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/platform_notify.py**:
  - The scope line is there only when the event has a domain or an environment, and the environment is kept.
  - A decision is news again after a new wait, also when the events name their own keys.
  - The key a fix closes comes from its own key, or from its id when its key is its own.
  - The outbox and the ledger must be one file.
  - Ids and titles are kept to 200 characters and details to 500.
  - A route can't be changed once made.
  - The ledger gives a key's kind and whether it is open, and marks and forgets a key outside a send's transaction.
  - A fix for a break that is closed already isn't posted.
  - A notice for a resting route is queued.
  - → `test_platform_notify_mutation.py`.

### Left

None.

# Mutation testing, batch 145

Views, search, grouping and paging over workload records (`workloads/query.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 109 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/query.py` | test_workload_query, test_workloads (+ test_workload_query_mutation) | 109 of 109 (109 + 0) | 97 / 12 | **89.0%** | 97/109 → 107/109 | none | 107 / 2 | **98.2%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/query.py**:
  - Each row lands in its own environment group. The existing test grouped one Fabric-only and one PC-only workload, so swapping the two groups gave the same list.
  - Prod comes before Test whatever order the deployments are in.
  - The default page is 100 rows.
  - Nobody signed in owns nothing (`False`, not just falsy).
  - A row without tags or actions has empty lists.
  - Search finds a deployment's environment and its label.
  - → `test_workload_query_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/query.py` | 19 | `'dev': 2` to `3` | Equivalent: Dev is still after Prod and Test and before any other environment. |
| `workloads/query.py` | 66 | the default order `9` to `10` | Equivalent: it is still after Prod, Test and Dev. |

# Mutation testing, batch 146

One list of running, queued and next work across this PC, each agent and Fabric (`workloads/queue.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 105 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/queue.py` | test_workload_queue (+ test_workload_queue_mutation) | 105 of 105 (101 + 4) | 81 / 24 | **77.1%** | 81/101 → 99/101 | 0/4 → 4/4 | 103 / 2 | **98.1%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/queue.py**:
  - A live agent's schedule that is due is next, with no note; an offline agent's at exactly now is missed, and a later one says it is from the last report.
  - The window ends at exactly 24 hours by default, for agents and for this PC.
  - A schedule on this PC that doesn't say whether it is enabled runs, and keeps its environment and empty text.
  - Fabric rows without names say what they are.
  - A shared schedule held by one agent is one row. One past due with a live agent is next, and one at exactly now with no agent reporting is missed.
  - Rows sort by state (running, unknown, queued, missed, next), then time, then name.
  - → `test_workload_queue_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/queue.py` | 153 | `'next': 4` to `5` | Equivalent: next still sorts after every other state. |
| `workloads/queue.py` | 163 | the default order `9` to `10` | Equivalent: every row has one of the five states, so the default is never used. |

# Mutation testing, batch 147

Who runs each workload and with what credential (`workloads/run_as.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 88 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/run_as.py` | test_run_as (+ test_run_as_mutation) | 88 of 88 (85 + 3) | 69 / 19 | **78.4%** | 69/85 → 84/85 | 0/3 → 3/3 | 87 / 1 | **98.9%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/run_as.py**:
  - A certificate expiring at exactly now is expired, at exactly 30 days is expiring, and later is ok.
  - An expiry with an offset keeps it, and one without is read as UTC.
  - A certificate whose dates couldn't be read is unknown, and so is a secret without a token; a secret with one is ok.
  - An older agent's credential kind follows its identity.
  - The newest record speaks whichever comes first, and on a tie the first one.
  - An agent in one environment names it.
  - This PC, Fabric jobs and Fabric schedules are live, and a schedule without names says so.
  - → `test_run_as_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/run_as.py` | 23 | `'expiring': 2` to `3` | Equivalent: expiring still sorts after expired and failed, the only other states listed. |

# Mutation testing, batch 148

The management records this PC keeps about workloads (`workloads/store.py`): assignments and their history, links from notebooks to Fabric items, actions, lines waiting for the platform's audit, and the collection cache.

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 94 mutants, so all of them ran. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/store.py` | test_workloads, test_workload_store_faults, test_sqlite_stores, test_store_upgrades, test_workload_audit (+ test_workload_store_mutation) | 94 of 94 (94 + 0) | 77 / 17 | **81.9%** | 77/94 → 94/94 | none | 94 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/store.py**:
  - The schema version is 1 and 10,000 actions are kept.
  - The file's folder is made with its parents.
  - Assigning returns what the workload now has.
  - History shows the newest 100 by default; actions and waiting audit lines page at 50.
  - A link with a part missing is not kept. The same item again writes no history, and another item does.
  - Unlinking a notebook that isn't linked gives empty text.
  - An action that doesn't exist is a `KeyError`. An action has a 12-digit id, and ends only with an ending status.
  - A reference is merged into the one kept.
  - A failed audit try keeps 500 characters of why.
  - → `test_workload_store_mutation.py`.

### Left

None.

# Mutation testing, batch 177

One module, run uncapped: every mutant, not a sample of 150. Batch 16 sampled 150 of its 515 mutants.
- Proving a notebook and replaying a failure: a proof's two runs, how they are compared, its verdict and receipt, proof rules, and opening, checking and saving replay bundles (`prove_service.py`).

The run was at `main` 82f8e2101 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902. `prove_service.py` hasn't changed since batch 16's run. It used `--cap 100000 --seed 7 --kinds all`, so all 515 mutants ran (511 point, 4 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. As batch 178 below explains, each pytest session got a new, empty base folder of its own. A mutant whose run times out counts as killed. One timed out: the loop that finds a free name for an opened bundle's notebook, with its condition changed, never ends.

The module's tests take several minutes together, far over the mutator's 120-second limit for one mutant. So the "before" figures come from four runs, as in batch 95:
- All 515 mutants ran with the 8 quicker files (the first 8 in the table, about 13 seconds together). 181 survived.
- The 181 ran again with group A: test_prove and test_wizard, which run whole proofs against a fake Fabric. Group A killed 36: what Fabric's packages are remembered as, receipts exported and saved, proof rules, the list of proofs, a proof's start, its two runs and its verification, deleting its outputs, and the wizard's notebook. The container restarted after 66 of the 181 had run. The other 115 ran on their own with `--only`, from the same commit and with the same files.
- The 145 left ran with group B: test_replay, which replays bundles in the notebook's own process. Group B killed 79: opening, previewing, listing and saving bundles, a replay's check, its tables and samples, its start, its result and its end, the sandboxes it remembers, and a replay notebook's cells waiting for the replay.
- The 66 left ran with group C: test_kernel_process. Group C killed 1: a proof measures the notebook's own process when notebooks run in their own process.

Mutants killed in any of the four runs count as killed "before". The "after" run gave the 65 survivors the first set and the new file. The groups weren't needed there.

Before the "after" run, the new tests were run in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `prove_service.py` | test_prove_service_edges, test_local_retention_gaps, test_replay_edges, test_receipts_edges, test_notebook_cells, test_prove_service_mutation, test_state_writes, test_debugging, then groups A, B and C above (+ test_prove_service_uncapped) | 515 of 515 (511 + 4), uncapped | 450 / 65 | **87.4%** | 449/511 → 504/511 | 1/4 → 2/4 | 506 / 9 | **98.3%** | 7 equivalent, 2 not killed |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **prove_service.py**:
  - A proof asks Fabric for news every 5 seconds. A replay bundle may be up to 100 MiB, and a bundle of exactly that size isn't too large.
  - A notebook's dependencies, the receipt redactions and the list of receipts (50 by default) come from their own modules.
  - Fabric's packages are remembered under the module's name, whether Fabric reported the module or its distribution. The file is written with `indent=1` and sorted keys, in a folder made with its parents.
  - A saved receipt says what it left out. It gives up after 999 taken names, and a bundle after 99. A bundle's file name keeps 60 characters of the notebook's name, and so does the notebook made from an opened bundle.
  - Bundles in Downloads are listed newest first, 50 at most, with the number of samples each has.
  - A replay's copy that is gone names the bundle's notebook. The live replay is looked up without making a session. A notebook whose extras can't be read isn't a replay.
  - A bundle without ids never attaches a lakehouse, even when the lakehouse it names still carries one.
  - A replay's check names the bundle's lakehouse when none is attached, and compares the bundle's environment, for the modules its code cells import, with this PC's.
  - A replay seeds `random` with 0, and the sandboxes it remembers keep the lakehouse's name. A sandbox that can't be deleted names its lakehouse.
  - A saved proof keeps its PC side without the cells' outputs, and only the last 200 proofs are kept. A saved proof is found by its id. Proofs are listed newest first, 20 at most.
  - Cancelling a proof that is verifying doesn't cancel a Fabric job. Cancelling it while Fabric runs ends it cancelled.
  - Verifying a table again reads that table, from the first differing row, and keeps no limitations when the compare gave none. A proof where neither side wrote a table compares only the time.
  - A proof's id ends in 6 hex digits. It seeds `random` with 0, counts its code cells from 1, and keeps a silent cell's outputs as `[]`, not `None`. Its Fabric side is the Python engine for a notebook without Spark. Fabric's elapsed time is in whole seconds, its total and job times in hundredths, and its start time is never below 0.
  - A published notebook that comes back without an id is found by its name.
  - A record that is busy is copied up to 20 times.
  - → `test_prove_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `prove_service.py` | 248 | an unreadable bundle's copy removed with `missing_ok=True` → `False` | Equivalent: the copy was written just before, so it is always there to remove (batch 16, line 258 then). |
| `prove_service.py` | 644 | `zip(cells, saved["cell_ids"], strict=True)` → `False` | Equivalent: saving a notebook gives one cell id for each cell, so the two always have the same length. |
| `prove_service.py` | 661 | the proof's thread `daemon=True` → `False` | Not killed: it changes only whether the app waits for a running proof when it exits, which no test here can see without ending its own process. |
| `prove_service.py` | 802 | `i < len(suffixes)` → `<=` (boundary) | Equivalent: the parameters bar gives a suffix for every cell, so `i` is always below the length. |
| `prove_service.py` | 806 | the first poll after a cell starts asks from version `-1` → `-0` | Equivalent: at version 0 nothing has been output yet, so both give no outputs; after it, both give them all (batch 16, line 816 then). |
| `prove_service.py` | 807 | `snap.get("outputs") or []` → `and []` on that first poll | Equivalent in the record: it changes only what the panel shows for a running cell, and the cell's final outputs come from a poll of their own (batch 16, line 817 then). |
| `prove_service.py` | 813 | the final poll asks from version `-1` → `-0` | Equivalent: a finished cell's version is past 0, so both give all its outputs. |
| `prove_service.py` | 815 | the final `snap.get("outputs") or outputs` → `and outputs` | Equivalent: the outputs seen last while the cell ran are the final poll's outputs, and when there are none both are empty (batch 16, line 825 then). |
| `prove_service.py` | 861 | `time.monotonic() - started < MAX_FABRIC_SECONDS` → `<=` (boundary) | Not killed: it differs only at the exact instant six hours have passed. |

# Mutation testing, batch 178

One module, run uncapped: every mutant, not a sample of 150. Batch 7 sampled 150 of its then 449 mutants.
- The local `notebookutils`: `fs` on this PC and in the attached lakehouse, credentials and Key Vault, `notebook.run`, the runtime context, Fabric item lists, `loadTable`, user data functions and the variable library (`notebookutils_local.py`).

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902. It used `--cap 100000 --seed 7 --kinds all`, so all 516 mutants ran (511 point, 5 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. `test_notebook_parity.py::test_spark_reads_files_and_groups_like_pyspark` reads `examples/`, so it was left out with `--deselect`. Two files from batch 7's set were left out to keep each mutant's run short: `test_keys.py` (16 s on its own, and it doesn't call this module) and `test_estate.py` (42 s). Four quick files that call the module were added: test_notebookutils_load_table, test_dbutils_parity, test_notebook_resources and test_notebook_parity.

No mutant's run timed out.

### Why this run was done again

The first uncapped run of this module gave false kills, so it was thrown away and its numbers are not used here. Each mutant's tests ran with one fixed `--basetemp` folder for the lane. pytest deletes that folder at the start of every session. Mutant 58 drops `_same_or_inside`'s answer for local paths (line 130). With it, `test_local_mv_into_itself` copies a folder into itself until the path is too deep. From then on, deleting the folder raised `RecursionError` at the start of every session, so every later mutant failed before any test ran and counted as killed. That run's timings show it: from mutant 59 on, every mutant was "killed" in about 10.5 s. The run here gave each pytest session a new, empty base folder of its own. A small plugin outside the committed files did this (`pytest_configure` sets `basetemp` to a fresh `tempfile.mkdtemp` folder). The broken run had 4 survivors. This run has 49.

Before the "after" run, the new tests were run in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebookutils_local.py` | test_notebookutils_local_mutation, test_notebookutils_local_edges, test_notebookutils_fs, test_notebookutils_helpers, test_notebookutils_parity, test_notebookutils_load_table, test_paging_complete, test_contract_onelake, test_dbutils, test_dbutils_parity, test_notebook_resources, test_notebook_parity (+ test_notebookutils_local_uncapped) | 516 of 516 (511 + 5), uncapped | 467 / 49 | **90.5%** | 466/511 → 508/511 | 1/5 → 5/5 | 513 / 3 | **99.4%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **notebookutils_local.py**:
  - `loadTable`: it returns True. An append keeps the table's schema and an overwrite replaces it. A folder's files are joined by name, with missing columns allowed. It needs an attached lakehouse, a table name that isn't blank and a known `pathType`. An empty folder says it has no files. Files of another format are named, five at most, then "and more".
  - `FileInfo` has a change time of 0 unless one is given. A local path and a OneLake path are never the same path (`False`, not just falsy).
  - `Files/` itself can't be changed, and nothing is sent.
  - `head` of a local file reads only its first bytes. `head` of 1 byte gives 1 byte. `head` of 0 bytes reads nothing, also from OneLake.
  - A folder named `Files` deeper down is looked up in its parent. Only the lakehouse's own `Files` and `Tables` are answered without a listing.
  - `put` without overwrite keeps what is there. `cp` of a folder needs `recurse`. `mv` onto something that is there needs `overwrite`. `mv` into itself is refused in `mv`'s own words.
  - A OneLake listing refused with 403 is raised as it is, without a second lookup. `rm` asks OneLake for one entry to see that a folder isn't empty.
  - An unknown `notebookutils` name is an `AttributeError`.
  - A Key Vault link whose port isn't a number isn't on the vault. A Key Vault 400 is an error with its code. `getSecret` asks once, with a 30-second limit and API version 7.4.
  - A token is valid until the second it expires, and payloads of every length get their padding back.
  - `notebook.run` gives 90 seconds by default, and passes a cell timeout only when one is set.
  - The notebook, lakehouse and table lists give 1,000 items by default. The default lakehouse is looked up in the workspace given.
  - The runtime context equals its copy. It isn't a pipeline run or a reference run, and its user name comes from `USERNAME` when `USER` isn't set.
  - A variable reference reads the library it names.
  - → `test_notebookutils_local_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebookutils_local.py` | 322 | `mv`'s `create_path` default `False` → `True` | Equivalent: `mv` never reads `create_path`. It is there only so Fabric's signature works. |
| `notebookutils_local.py` | 232 | a local folder's size in `_entry` 0 → 1 | Equivalent: nothing reads a folder's size there. `mv` compares a folder's files, not its size. |
| `notebookutils_local.py` | 236 | the size of the `Files` or `Tables` root entry 0 → 1 | Equivalent, as batch 7 found: nothing reads that size, and moving `Files` itself is refused. |

# Mutation testing, batch 179

One module, run uncapped: every mutant, not a sample of 150. Batch 12 sampled 150 of its then 411 mutants.
- The local pipeline runner: the checks before a run, running each list of activities, retries and policy timeouts, mocks, child pipelines, cancels and the run record (`orchestration/runner.py`).

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902. It used `--cap 100000 --seed 7 --kinds all`, so all 419 mutants ran (411 point, 8 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. As in batch 178, each pytest session got a new, empty base folder of its own. The 14 files below run in about 35 seconds together. Batch 12's other three files, `test_pipeline_script`, `test_pipeline_data` and `test_pipeline_schedules`, were left out to keep each mutant's run short. Together they take about 18 seconds, and their subjects are other modules. No test in the set reads `docs/`, `examples/` or `app/`, so no `--deselect` was needed.

Before the "after" run, the new tests were run in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests also passed 25 runs in a row, under the load of the other lanes.

A mutant whose run times out counts as killed, as in earlier batches. 25 timed out, each a wait or loop that no longer ends: the run loop of one list of activities (lines 389, 392, 411), waiting for a parallel slot (430), the retry loop and its policy timeout (447, 457, 473 to 492), the record of a step that is running (501, 507), and an activity's wait (622 to 629, 806). One more timed out in the "after" run: `left <= 0` to `< 0` in an activity's wait (line 625). With the new test's clock stopped, a wait of 0 seconds then never ends. With a real clock, a left of exactly 0 would be followed by a negative one.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/runner.py` | test_orchestration_runner_mutation, test_pipeline_runner_edges, _service_edges, _fabric_parity, test_cli_tenant_commands, test_pipeline_gotchas, _definitions, _data_edges, _notebooks_edges, _script_edges, _adapters_edges, test_orchestration_definition_edges, _runs_edges, test_pipeline_runs (+ test_orchestration_runner_uncapped) | 419 of 419 (411 + 8), uncapped | 374 / 45 (25 timeouts) | **89.3%** | 371/411 → 392/411 | 3/8 → 6/8 | 398 / 21 (26 timeouts) | **95.0%** | 18 equivalent, 3 timing only |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **orchestration/runner.py**:
  - Child pipelines nest at most 8 deep: a pipeline that runs itself is refused with 7 child levels in the message.
  - An integer variable declared without a default starts at 0, for both `Int` and `Integer`.
  - A cancelled state matches no dependency condition (`False`, not just falsy).
  - A skipped step is saved as soon as it is skipped, while the run is still going, and a step is saved when it ends, not when it starts.
  - A notebook step's output is what its notebook gave.
  - Four activities run at once by default.
  - A retry waits the interval the policy gives, and 30 seconds when it gives none.
  - A container cancelled while its steps run is cancelled, not failed.
  - A list asked for after a cancel records nothing.
  - A child pipeline is waited on unless the call says not to.
  - A step that never started made no attempt.
  - An activity that isn't local is refused, even when its adapter could run.
  - Steps still waiting for a parallel slot at a cancel end cancelled, without starting.
  - Only `SecureString` parameters are hidden in the record.
  - An inactive step marked Skipped, with nothing before it, doesn't fail the run.
  - A run without notebooks says it ran on this PC.
  - A run's seconds are kept to the millisecond.
  - An Until's deadline has passed at that very moment, and a wait that ends exactly at its deadline isn't a timeout. A fake clock pins both.
  - → `test_orchestration_runner_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `orchestration/runner.py` | 326, 408, 664, 719, 768, 805 | a thread's `daemon=True` to `False` (the run's timer, each step, each ForEach item, a child's watcher, a detached child and a lakehouse read) | Equivalent in a run: each of these threads is cancelled or joined before the run ends. The flag only matters when the process exits. |
| `orchestration/runner.py` | 720, 768 | a thread's name takes 9 characters of the child's id, not 8 | Equivalent: nothing reads thread names. They only help debugging. |
| `orchestration/runner.py` | 577 | `_leaves_succeed`'s inner `return False` to `None` | Equivalent: its only caller is `all(...)`, where `None` is false too. |
| `orchestration/runner.py` | 490 | the `return "failed"` after the retry loop to `None` | Equivalent: it can't be reached. Every pass of the loop returns, or continues to a next pass, and the last pass always returns. |
| `orchestration/runner.py` | 454, 455 | the default of `policy.get("retry", 0)` 0 to 1, and of `policy.get("retryIntervalInSeconds", 30)` 30 to 31 | Equivalent: each default is used only when the key is missing, and then the `isinstance(..., int)` beside it is false, so the other branch (0, 30) is taken. |
| `orchestration/runner.py` | 450 | `getattr(adapter, "container", False)` default to `True` | Equivalent: every adapter that can run is a `LocalAdapter`, which sets `container`. |
| `orchestration/runner.py` | 249 | `getattr(entry.adapter, "can_execute", False)` default to `True` | Equivalent: the registry refuses a local entry whose adapter doesn't set `can_execute`, and every other entry is refused by its classification first. |
| `orchestration/runner.py` | 411 | `and` to `or` in the test that ends a list's run loop | Equivalent: the loop runs under the lock that steps take to set their state. Each pass starts every step whose dependencies have ended. So a pass never ends with no step running and some step still without a state. |
| `orchestration/runner.py` | 660 | `or` to `and` in ForEach's wait for a free place in its batch | Equivalent: a cancel stops the running items too, so they free their places. The check right after the wait then starts no more items. |
| `orchestration/runner.py` | 710 | a child run's depth `depth + 1` to `depth - 1` | Equivalent: the nesting limit is enforced when the top-level run is checked, and that check builds its own probes with the right depth. At run time the depth is only compared with 0, which a child's depth never is either way. |
| `orchestration/runner.py` | 730 | `child.state == "cancelled" or self.run.cancelled` to `and` | Equivalent: a child waited on is cancelled only by its watcher, and only when an enclosing Until's deadline has passed. The `ActivityFailed` that the mutant raises then becomes `cancelled` in `_attempts`, with no error or output kept. |
| `orchestration/runner.py` | 749 | the watcher's time left `deadline - now` to `deadline + now` | Timing only: the watcher then checks every 0.2 s, not at the deadline. A child is stopped at most 0.2 s later. A test would depend on the machine's speed. |
| `orchestration/runner.py` | 746, 809 | `>=` to `>` where a child's watcher and a lakehouse read compare the clock with the deadline | Timing only: they differ only when the clock reads exactly the deadline, and the next check, at most 0.2 s later, stops it either way. Both loops wait on the real clock, so a fake one can't pin them. |

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
