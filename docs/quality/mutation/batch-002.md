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

