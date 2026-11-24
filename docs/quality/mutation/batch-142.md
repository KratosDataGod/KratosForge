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
| `workloads/notify.py` | 96 | `end > now` to `>=` | Reachable only when quiet hours end on a second other than :00. agent.toml refuses such a time, but platform notice settings accept one (`22:00-06:00:30`), and a message held then waits until 06:00 the next day instead of 06:00:30. Reported for a decision, not changed here (tests only). Answered since: it was a bug, and the Governance fix, #3042, makes quiet hours accept only `H:MM-H:MM` and keeps the seconds in `until()`. |

