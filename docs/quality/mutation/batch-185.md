# Mutation testing, batch 185

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). It has no `<` or `>` comparison, so it has no boundary mutants.
- The engine picker behind a notebook: connections and Settings > Connections, the confirm and risk check a remote engine asks for, switching engines, Databricks runs, billing, sync pairs and bundles, and the threads a session and a cell run on (`engines_service.py`).

Batch 15 sampled 150 of this module's 391 mutants then (86.7% before, 100% after). This run ran all 391 of 391.

The run was at `main` ad1ac4cf8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 and the method of batch 182: tests first passed on the unmutated copy, paths relative to `sidecar/`, and the lane's own `--basetemp` (batch 182 says why). The set is every test file that calls the module, as in batch 15, and it takes about 7 seconds. No `-k` filter or `--deselect` was needed. Three kills picked at random were checked by hand, as in batch 182, and each failed real tests (11, 11 and 10).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways.

A mutant whose run times out counts as killed. Four timed out in the first run, as in batch 15: two make `_new_id`'s loop never end (line 110), and two make the loop that picks a free connection id never end (line 496). One more times out in the "after" run: `n += 1` to `+= 0` in that loop (line 498), which a third connection from the same CLI profile now reaches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines_service.py` | test_databricks_billing, test_databricks_bundles_service, test_databricks_runs_service, test_databricks_sync_service, test_engine_connections_settings, test_engines_service, test_engines_service_edges, test_engines_service_mutation, test_fabric_spark (+ test_engines_service_uncapped) | 391 of 391 (391 + 0), uncapped | 357 / 34 | **91.3%** | 357/391 → 388/391 | none | 388 / 3 | **99.2%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines_service.py**:
  - Removing a sync pair answers with its id, and removing a connection with the profile and the connection.
  - Adding a connection clears the profile's last check. A saved CLI connection isn't serverless. A new profile gets a 14-character id.
  - A third connection from the same CLI profile gets `-3`, and a long CLI profile name is cut to 36 characters before its number. Replacing one connection keeps the others.
  - Settings show a Databricks warehouse id and a Snowflake warehouse, each profile's name (a damaged one's too), and only the active profile as active. A connection row carries its profile's remote compute policy.
  - Discovery reads Unity Catalog unless asked not to, and runs are of the last day unless asked. Runs that can't read the signed-in user are still signed in.
  - Billing runs when the sign-in can't name its user, as "this PC's user".
  - Bundle validate, plan and deploy pass the allowed commands and the typed target on.
  - The profiles folder can be asked for twice.
  - An engine that can't run cells yet is refused with its plan. Fabric Spark needs both the workspace and the lakehouse.
  - Leaving Python for another engine needs a decision (close or keep), and going back to Python from a local engine plans to close it.
  - A profile that allows the engine by policy needs no confirm.
  - The risk check names the cluster's state ("terminated", and "stopped" when it has none), and one that can't read says the HTTP status or the kind of error.
  - A cell that raises says how long it ran.
  - → `test_engines_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines_service.py` | 80, 633 | `_MAGIC.sub(..., count=1)` to `count=0` | Equivalent: `_MAGIC` starts with `^` and has no multiline flag, so it can only match at the start of the text, once. |
| `engines_service.py` | 686 | `current is not None and current != engine` to `or` | Equivalent: the new cases are a notebook with no engine yet, or the same engine again. For both, `transition` answers `needed: False`, so no `SwitchNeeded` is raised either way. |

