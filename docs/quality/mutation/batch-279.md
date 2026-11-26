# Mutation testing, batch 279

One module that had no score before, run with `--kinds all`:
- Platform notices in the app: sending governance's events to Teams, a Power Automate flow or email, the routes and the company mail server in settings, and the bell's list of notices still open (`platform_notify_service.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 104 mutants, so all were run. The sample held no boundary mutant. No mutant's run timed out.

The first run used the 13 test files that name this module, and scored 72 / 104 (69.2%). The "before" figures are from a second run that also has test_sealed_settings, test_central_service_cov3, test_shortcut_service_paths and test_requests_core_four, which reach the routes and the mail server through the service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `platform_notify_service.py` | test_platform_notify, test_notice_locked_outbox, test_notice_failures, test_deliver, test_governance_notices, test_incident_desk, test_incident_desk_mutation, test_incident_partial_work, test_incident_service_mutation, test_request_left_in_fabric, test_retention_edges, test_dataops, test_alert_to_incident_e2e, test_sealed_settings, test_central_service_cov3, test_shortcut_service_paths, test_requests_core_four (+ test_platform_notify_service_mutation) | 104 of 104 (104 + 0) | 74 / 30 | **71.2%** | 74/104 → 103/104 | none sampled | 103 / 1 | **99.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **platform_notify_service.py**:
  - Events that aren't a list are refused with "events is a list".
  - With no route, nothing is counted as sent, queued or held.
  - Quiet hours and their exemptions are saved and shown back, and are empty when none are set.
  - A test sent to a route that isn't there is refused, and nothing is saved or posted.
  - An email route is shown as email.
  - Saving routes and the mail server returns what is set. The mail server defaults to port 587. Forgetting it returns that none is set.
  - A relay without a user name never shows or sends a password, even one an older build left in Credential Manager.
  - A password that can't be read shows as not saved.
  - The bell shows each field of a notice: title, whom, domain, environment and detail. It shows blanks for a notice whose row is gone.
  - The bell gives the latest reason a notice wasn't taken.
  - A notice opens where it was recorded. When that view or tab isn't a real one, it opens on its kind's page.
  - Closed notices are kept a year.
  - → `test_platform_notify_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `platform_notify_service.py` | 72 | `zip(..., strict=True)` to `False` | Equivalent: `send` returns one result for each event it was given, so both lists are always the same length. |
