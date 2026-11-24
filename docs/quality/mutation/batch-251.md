# Mutation testing, batch 251: workloads/notify.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 142 sampled 150 of its then 221 mutants (83.3% before, 96.7% after).
- Telling Teams channels when incidents open, recover or escalate: routes, quiet hours, digests, the SQLite outbox, a failing route's rest, and posts that may have arrived and are never sent twice (`workloads/notify.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 376 of 376 mutants ran (365 point + 11 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The "before" run gave pytest `--basetemp` in a folder of this run's own, and that folder was checked afterwards: no mutant left a deep folder behind that could have failed the next mutants at startup. The "after" run gave each pytest session a new temporary folder of its own (a small plugin, not committed). A few killed mutants were run again by hand with that plugin, and each was killed again.

The set is batch 142's files and the other files that send, keep or read notices through the module: test_notice_failure_kinds, test_notice_locked_outbox, test_notice_retries, test_platform_notify_mutation, test_alert_to_incident_e2e, test_schedules, test_governance_notices, test_retention_edges, test_sqlite_stores, test_store_upgrades, test_workload_store_faults, test_sqlite_connections_closed and test_shortcut_watch. test_redact and test_sqlite_durability were left out to keep each mutant's run short; they touch the module in a few lines. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/notify.py` | test_notify_mutation, test_notify_delivery, test_notice_failure_kinds, test_notice_failures, test_notice_locked_outbox, test_notice_retries, test_platform_notify, test_platform_notify_mutation, test_alert_to_incident_e2e, test_incidents, test_schedules, test_governance_notices, test_agent_clock_jumps, test_retention_edges, test_sqlite_stores, test_store_upgrades, test_workload_store_faults, test_sqlite_connections_closed, test_shortcut_watch (+ test_notify_uncapped) | 376 of 376 (365 + 11) | 330 / 46 | **87.8%** | 324/365 → 355/365 | 6/11 → 11/11 | 366 / 10 | **97.3%** | 10 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/notify.py**:
  - What a send counts when one outbox step fails: a trim that fails dropped 0, a flush that fails sent 0 (not 1, and not `None`), a count that fails leaves 0 waiting, and a send with no outbox at all dropped 0.
  - An outbox that can't say which routes rest is not used for the rest of the send: nothing it holds is sent, the new notice is posted once without being kept, and one refused then is lost, not kept to send later; nothing is counted waiting.
  - A notice that can't be kept (`sending` fails) and isn't taken is lost, not queued, and each such notice for a route that already failed counts once.
  - Sent messages whose rows can't be deleted yet: they aren't listed or counted as waiting; one read back through `pending` is kept on disk by its id and created time; a kept id whose row now holds another message (a reused id) doesn't hide it; a kept id another Outbox settled meanwhile is left alone; a new outbox file removes the kept-ids file left from the one before; entries that aren't (id, time) pairs are ignored; and a message that goes logs no warning (no file to remove).
  - An escalation to a route with no address, or to one that doesn't exist, goes nowhere.
  - Five changes are five messages, six are a digest, and a digest names "1 new problem" in the singular.
  - The audit line keeps 300 characters of the error.
  - Quiet hours end at their last minute (01:00-06:00 and 22:00-06:00 don't hold at 06:00), and hours that start and end at the same minute (22:00-22:00) hold all day: at 10:00 until 22:00, and at 22:00 until 22:00 the next day. That last is the code's reading of such a span, pinned as it is; neither the agent nor the settings refuse one.
  - Quiet hours, like routes, can't be changed once made (a frozen dataclass).
  - A post that never connected (`ConnectTimeout`) is plainly `False`, a read of the outbox that fails gives `idempotent` `False`, and a failure that can't be noted gives `route_failed` 0.0, as their signatures say.
  - → `test_notify_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/notify.py` | 94 | `return False` in the `ImportError` branch to `None` or `True` (2 mutants) | Equivalent: requests and urllib3 ship with the engine, so the import never fails (the line is `pragma: no cover`). |
| `workloads/notify.py` | 294, 297 | `lastrowid or 0` to `or 1` (2 mutants) | Equivalent: an INSERT always gives a row id, so the fallback is never used. |
| `workloads/notify.py` | 363 | `_settle_gone`'s `return True` with nothing kept, to `None` or `False` (2 mutants) | Equivalent: only `done` reads the answer, and it keeps an id just before, so nothing kept never reaches it. |
| `workloads/notify.py` | 371 | `_settle_gone`'s `return False` after a failed delete to `None` | Equivalent: `done` only tests whether the answer is true. |
| `workloads/notify.py` | 399 | `_is_gone`'s `return False` to `None` | Equivalent: every caller only tests whether the answer is true. |
| `workloads/notify.py` | 436 | `min(failures - 1, 20)` to `21` | Equivalent: 60 s × 2^20 is far beyond `BACKOFF_MAX` either way, and the wait is capped at `BACKOFF_MAX`. |
| `workloads/notify.py` | 471 | `per_route.get(route.name, 0)` to `1` in the check | Equivalent: it only changes the first check for a route, where 1 is still below `MAX_PER_TRY` (25). |

