# Mutation testing, batch 324: platform_notify_service.py (every mutant)

One module, run uncapped: every mutant. It had not been mutation tested before.
- Platform notifications in the app: the `platform_notify` call, the routes and the mail server in settings, the bell's open notices, and how long dedupe keys are kept (`platform_notify_service.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` and `--cap 100000 --seed 7 --kinds all`, so all 104 of 104 mutants ran (104 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Temporary folders were handled and checked as in batch 320.

The set is the files that call the module's methods and run in a copy of `sidecar/` alone: test_platform_notify, test_governance_notices, test_notice_failures, test_notice_locked_outbox, test_deliver, test_incidents, test_incident_desk and test_incident_desk_mutation. test_rpc_surface was left out: two of its four tests fail in a copy of `sidecar/` alone, as the mutator runs them, so no mutant could be scored with it in. The 32 survivors were also run against those files plus the other files that name the module (test_alert_to_incident_e2e, test_dataops, test_incident_partial_work, test_incident_service_mutation, test_request_left_in_fabric, test_requests_core_four, test_retention_edges and test_sqlite_stores): none of them was killed. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `platform_notify_service.py` | test_platform_notify, test_governance_notices, test_notice_failures, test_notice_locked_outbox, test_deliver, test_incidents, test_incident_desk, test_incident_desk_mutation (+ test_platform_notify_service_uncapped) | 104 of 104 (104 + 0) | 72 / 32 | **69.2%** | 72/104 → 103/104 | none | 103 / 1 | **99.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **platform_notify_service.py**:
  - Events that aren't a list are refused as such, not as "event 1".
  - With no route set, the answer counts 0 sent, 0 queued and 0 held.
  - `platform_notify_save`, `mail_server_save` and `mail_server_forget` return what the UI shows.
  - The settings read back: each route's kinds and domains, how it sends (Teams or email), the quiet hours and their exemptions, empty lists and text when nothing is set, and `unreadable` for a route this Windows account can't decrypt.
  - A test message to a route that isn't there is refused with a `ValueError` and nothing is saved (an `or` to `and`, and the `raise` removed).
  - The mail server's port is 587 when none is given, and the settings say `set`.
  - A password counts as saved only for a server with a user name, and not when Credential Manager can't be read.
  - A test email with no password given uses the saved one.
  - The bell shows a notice's domain, environment and detail (the column indexes and the `if n` guards), and still shows an open key whose details row is gone, opening its kind's page.
  - Where a notice opens: what was recorded when it names a known view and tab, otherwise its kind's page, also with nothing recorded.
  - The bell gives the last error, not the first.
  - A closed dedupe key is kept 365 days and then deleted (a key 100 s past and one 100 s short of it).
  - → `test_platform_notify_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `platform_notify_service.py` | 72 | `zip(..., strict=True)` to `strict=False` | Equivalent: `send` returns exactly one result per event, so the two lists are always the same length. |
