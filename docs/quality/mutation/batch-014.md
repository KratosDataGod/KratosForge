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

