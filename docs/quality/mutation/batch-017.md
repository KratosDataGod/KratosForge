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

