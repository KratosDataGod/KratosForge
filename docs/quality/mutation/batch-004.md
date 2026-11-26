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

