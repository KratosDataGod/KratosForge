# Mutation testing, batch 378

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- What the app's Settings > Integrations page calls: the switches, connected tools, approvals, the local API's process and Test connection (`integrations/app_service.py`).

The run was at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 197 of 197 mutants ran (192 point + 5 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_integrations_app_service_cov3, test_integrations_peer, test_local_retention_gaps, test_integrations_http_boundary, test_integrations_app_edges, test_integrations_app, test_integrations_http_guard, test_integrations_permissions and test_integrations_http, about 35 s a session. About fifteen other files call these methods through the service; test_integrations_multi_client (20 s), test_integrations_audit (7 s), test_integrations_audit_every_call (still running after 9 minutes) and the service-level ones were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used. In both runs two mutants' runs timed out and count as killed: both turn the wait for the local API to start (line 119) into a loop that doesn't end. Run again on their own with a 600-second limit, with the "before" set and with the "after" set, both mutants (21 and 67) were killed by failing tests each time, so the numbers stand.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/app_service.py` | test_integrations_app_service_cov3, test_integrations_peer, test_local_retention_gaps, test_integrations_http_boundary, test_integrations_app_edges, test_integrations_app, test_integrations_http_guard, test_integrations_permissions, test_integrations_http (+ test_integrations_app_service_mutation_a) | 197 of 197 (192 + 5) | 161 / 36 (2 of the killed timed out) | **81.7%** | 161/192 → 192/192 | 0/5 → 5/5 | 197 / 0 (2 timed out) | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/app_service.py**:
  - The limits and defaults: the local API is waited for 15 s (and not at all at 0 s), a stop waits 10 s, the log is rolled past 5 MiB and not at its cap, ports 1024 and 65535 are allowed and 1023 and 65536 refused, an HTTP tool's test waits 5 s and a stdio tool's a minute, approvals list 50 and activity 100.
  - The last line of the log is read from the start of a short log, from the last 4096 bytes of a long one, and cut to 500 characters.
  - The organization's switch, when off, turns off the status, the local API's "allowed" and the approval prompt's poll, and the launcher is written only while integrations are on.
  - A tool's workspaces are kept, and the audit check names its folder.
  - Activity: a started line drops out only when its own outcome line is there; a full page looks for unfinished approvals back to its oldest line; unfinished approvals take 0 s and sort newest first.
  - A stdio tool's test counts its tools, and gives the protocol version, or an empty string when the client gives none.
  - → `test_integrations_app_service_mutation_a.py`.
