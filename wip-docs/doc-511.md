# Mutation testing, batch 511

A re-score of `agent.py` on today's `main`, with `--kinds all`. It is the headless agent: its schedules and queue, the jobs it runs with their leases, the audit lines and Teams alerts, the shortcut check, and holding its control folders. It was last scored in batch 17 at **98.7%** (point mutants only), and its boundary mutants alone in batch 163 at **90.0%**.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has 336 mutants, so a sample of 150 was run.

The test set was the files of batches 17 and 163: test_agent, test_agent_clock_and_folder, test_agent_clock_jumps, test_agent_config_edges, test_agent_crash_report, test_agent_edges, test_agent_fleet, test_agent_queue_bound, test_agent_recovery, test_agent_silent_failures, test_agent_status, test_agent_takeover, test_cli_local_commands, test_incident_failures, test_incidents, test_jobqueue_concurrency, test_logs_json, test_mailbox, test_mailbox_edges, test_notify_delivery, test_schedule_clock_changes, test_schedule_edges, test_schedule_edits, test_shared_schedules, test_shortcut_watch, test_agent_mutation and test_agent_boundary. As in batch 163, eight tests that fail in the copy of `sidecar/` were left out with `--deselect`: seven in test_mailbox (the pipeline request, run once, cancel file, cancel between polls, cancelling a running request, the notebook's timeout and its pipeline run name) and `test_a_waiting_request_is_cancelled_by_its_cancel_file_and_is_never_queued` in test_agent_queue_bound.

One mutant's run timed out: `while running:` negated at line 1034, where the agent waits for its running jobs as it stops. With none running, the loop never ends. It was run again alone with a 1,200-second limit and timed out again, so it counts as killed.

The new tests reach no Fabric, OneLake, Teams or network; the shortcut test uses test_shortcut_watch.py's `world`, which fakes them. Before the "after" run, they passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent.py` | the 27 files of batches 17 and 163 (+ test_agent_mutation_b) | 150 of 336 (147 + 3) | 142 / 8 | **94.7%** | 139/147 → 147/147 | 3/3 → 3/3 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **agent.py**:
  - A control folder the agent released isn't held again when its control files are built again (as a failed status write makes it), so another agent can hold it.
  - A job's lease is renewed every third of it.
  - A Prod job whose audit line can't be written is alerted only when it has no tries left.
  - A job stopped part way, by a lost lease or by another agent taking its request over, is audited with the seconds it ran.
  - An alert carries the first 1,500 characters of the error.
  - A shortcut message left out is logged with its id cut to 200 characters.
  - → `test_agent_mutation_b.py`.

### Left

None.
