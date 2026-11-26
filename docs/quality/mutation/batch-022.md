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

