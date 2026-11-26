# Mutation testing, batch 122

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Integration settings, connected tools and approvals in their SQLite store: settings, tools and their tokens, requests, answers and uses (`integrations/store.py`).
- Learn: a module and its lab, a check recorded in Profile, hints, decision patterns, dismissing a finding and Try the fix (`learn_service.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_sqlite_durability.py` names `docs/` only in its docstring, so it runs in the copy. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` reads `docs/`, so it was left out with `--deselect`. `test_decision_patterns.py` was left out as a whole: three more of its tests fail in a copy of `sidecar/` on its own, since they read the patterns page in `docs/`. A mutant whose run times out counts as killed; none timed out.

The store is used by almost every integrations test. The run used the nine files that work with the store most directly. test_integrations_executions (about 40 s), and the MCP, HTTP and conformance tests, which start servers, were left out to keep each mutant's run short. So the "before" figure is for those nine files.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/store.py` | test_integrations_permissions, test_sqlite_stores, test_store_upgrades, test_integrations_audit, test_integrations_workspace_scope, test_local_retention_gaps, test_sqlite_durability, test_store_crash_kills, test_integrations_peer (+ test_integrations_store_mutation) | 112 of 112 (111 + 1) | 85 / 27 | **75.9%** | 85/111 → 111/111 | 0/1 → 1/1 | 112 / 0 | **100%** | none |
| `learn_service.py` | test_learn_service, test_learn_fixes, test_learn_fix_checked, test_learn_team, test_learn_guides (+ test_learn_service_mutation) | 59 of 59 (59 + 0) | 46 / 13 | **78.0%** | 46/59 → 59/59 | none | 59 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/store.py**:
  - A request waits 300 s, an approval must be used within 600 s, arguments are shown up to 20,000 characters, uses are kept 90 days, and the default HTTP port is 47615.
  - Changing a setting returns every setting, and an unknown setting is refused and not saved.
  - Changing a tool returns it, with its name stored as given, its grants and auto lists sorted and without repeats, and its workspaces in the order given. A change to any other field, or to a tool that isn't there, is refused.
  - A new token is returned and works; a tool that is gone or disconnected gets none, and its old token stops working.
  - The same call has the same hash whatever the order of its arguments.
  - Ids have ten hex digits, and tokens 43 URL-safe characters after `fdt_`.
  - Disconnecting a tool, or answering a request, that isn't there is refused.
  - The request list shows 50 by default, and the list of uses 200.
  - Arguments of exactly 20,000 characters are shown as they are; longer ones are cut to 20,000.
  - → `test_integrations_store_mutation.py`.
- **learn_service.py**:
  - A module shows each exercise's number of hints, and its size ("S" when none is given) and datasets (none when none are given).
  - Saving an answer says it was saved.
  - Only a passed check is recorded in Profile, and what it earned is returned.
  - The first hint is level 1.
  - The decision patterns are the shipped ones.
  - A rule whose id starts with "NB" is checked against the notebook policy, any other against the SQL one, and where and the line reach the dismissal as given.
  - Try the fix hands the findings on (none when none are given) and returns the proposal.
  - → `test_learn_service_mutation.py`.

### Left

None.

