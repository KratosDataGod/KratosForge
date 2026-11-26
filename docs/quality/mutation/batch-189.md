# Mutation testing, batch 189

One module, run uncapped: every one of its mutants, not a sample of 150.
- Run requests from Fabric pipelines, the agent's side of the mailbox in `LH_Meta`: claiming, checking the pipeline run, following the job, waiting for room, takeover, and the lock on a local control folder (`mailbox.py`).

Batch 14 sampled 150 of this module's 416 mutants then (82.0% before, 96.0% after). This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 432 of 432, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` ad1ac4cf8 (2026-11-18); `mailbox.py` is unchanged on `main` bdf2c1775, where the new tests pass too. It used `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy, and each pytest run had its own `--basetemp` folder, so another run's leftover temporary files could not fail it. A first run was thrown away: until about 23:53 UTC a too-deep folder another run left in the shared pytest temporary folder made every pytest session on the machine end with an error, which would have counted mutants as killed. A few "killed" mutants of the run below were run again by hand to see a test really fail.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Seven `test_mailbox.py` tests read `examples/pipeline/run_on_agent.ipynb`, so they were left out with `--deselect`, as in batch 14: `test_a_pipeline_request_runs_and_reports_back`, `test_the_same_request_runs_once_however_often_it_is_sent_or_read`, `test_a_cancel_file_stops_a_request_before_it_runs`, `test_a_cancel_that_arrives_between_polls_stops_the_worker_before_it_starts`, `test_cancelling_a_running_request_lets_the_run_finish`, `test_the_notebook_gives_up_cancels_and_fails_after_its_timeout` and `test_the_notebook_names_its_pipeline_run_when_given_one`.

Batch 14 also ran the agent's test files (test_agent, test_agent_clock_and_folder, test_agent_queue_bound, test_agent_recovery, test_agent_silent_failures, test_agent_status, test_agent_takeover, test_shared_schedules, test_schedule_overlap, test_not_set_up, test_cli_local_commands). Together they take about 35 s, three times the rest, so they were left out of this run to fit every mutant in; the score counts only the files given. The new tests kill what they would have: for example the refusal of a second agent on a local control folder, which `test_agent_clock_and_folder` also checks. A mutant whose run times out counts as killed; none timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mailbox.py` | test_mailbox, test_mailbox_edges, test_mailbox_more_edges, test_mailbox_mutation, test_lease_clock_skew, test_paging, test_contract_onelake, test_sqlite_connections_closed (+ test_mailbox_uncapped) | 432 of 432 (416 + 16), uncapped | 337 / 95 | **78.0%** | 335/416 → 409/416 | 2/16 → 16/16 | 425 / 7 | **98.4%** | 7 equivalent |

The "after" figures are from two `--only` runs on the survivors: the second ran the three the first left, once more tests were added.

No bugs were found.

### What the survivors were, and the tests that now kill them

- **mailbox.py**:
  - A request that can't be read yet waits less than a minute, then is rejected; a run lookup Fabric couldn't answer is tried again for less than fifteen minutes, then the request is rejected.
  - A request may be 64 KiB (65,536 bytes) and no more, and `submitted_by` 200 characters.
  - Timeouts, throttling and server errors (408, 429, 500, 502, 503, 504) are tried again; 400, 404, 409, 501 and 505 are not.
  - A checked run records Fabric's status and start time; one exactly a day old, or exactly five minutes ahead, still vouches; an older one says how many hours ago it started (26, for 25.5).
  - A run vouches for one request for exactly a day, and one taken exactly a day ago is still remembered.
  - `cascade` is kept only when it is true or false, with one entity named.
  - An agent that always claims takes a request with no worker free; a request is queued with its entities, backfill and cascade; a deadline of exactly now hasn't passed, at the door, when queueing or for a queued job.
  - A status that already says what the job says isn't written again; a written change is logged; a refused write checks whether another agent took the request over.
  - A request never queued ends cancelled or expired with its reason and without `waiting`, and a job the pipeline cancelled is cancelled, not expired. A request waiting for room says why once.
  - This agent's queued copy of a request another agent has is cancelled and logged, a running one is left; a poll hands such a request over, and takes it over only when the agent takes over.
  - An agent with no status record, or whose `stale_after` isn't a time, is not lost; one is lost only past `stale_after` plus a minute, and on OneLake's clock only with both a store time and a heartbeat, past its freshness plus the minute.
  - A request taken over keeps what it asked, drops the last agent's run, is logged, and then follows this agent's job. At least one agent may hold a request.
  - `taken_over` names the agent that has the request now, and is empty for a job that isn't this agent's pipeline job.
  - The refusal on a held control folder names the holder's process and host (cut to 64 characters) only when it has them; a second hold is refused; a folder that exists, or whose parents don't, can be held; a shorter holder record replaces a longer one whole.
  - A OneLake environment without `LH_Meta` is not set up yet, and a OneLake listing waits a minute for an answer.
  - → `test_mailbox_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `mailbox.py` | 134 | the lock file's `seek(0)` → `seek(1)` before locking | Equivalent here: `flock` locks the whole file wherever the position is, and after `truncate(0)` the record is written from the position, so the file reads back as a NUL, a space and the record, which `_holder` reads from byte 1 the same. On Windows it would lock byte 1 instead of byte 0, still one byte no other agent can lock. |
| `mailbox.py` | 145 | `truncate(0)` → `truncate(1)` | Equivalent: the record is written from byte 0 and is longer than one byte, so the byte kept is overwritten. |
| `mailbox.py` | 156 | the lock holder read from byte 1 → byte 0 | Equivalent: byte 0 is a space, which `json.loads` skips (as batch 14 found). |
| `mailbox.py` | 157 | the holder read as 4,096 bytes → 4,097 | Equivalent: a holder record is far smaller. |
| `mailbox.py` | 218 | a Fabric time's fraction cut to 6 digits → 7 | Equivalent: Python 3.12's `fromisoformat` takes the seventh digit and drops it. |
| `mailbox.py` | 411, 418 | `e.get("at") or 0` → `or 1` | Equivalent: every entry has a numeric `at`: entries without one are dropped on loading, and new ones always get it. |

