# Mutation testing, batch 15

Three modules no open pull request is changing:
- The engine picker behind a notebook: connections, the confirm and risk check a remote engine asks for, switching engines, Databricks sync pairs, and the threads a session and a cell run on (`engines_service.py`).
- OneLake shortcuts: the shortcut center, its history, repointing, finding a moved target, undo, and shortcut requests (`shortcut_service.py`).
- The Estate X-ray service: a scan and its cost findings, Optimize and Vacuum with their risk check, leftover Dataflow Gen2 staging lakehouses, and adopting a lakehouse into the metadata project (`xray_service.py`).

The run was at `main` e0187cd1a (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 13 (sample cap 150, seed 7), on DuckDB 1.5.6. All three modules have more points than the cap, so 150 of each were run. It ran beside batch 14, which took other modules.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test passed both ways, so no run below can score a false 100%. One test file was left out of the `shortcut_service.py` runs because two of its tests read the app's own files under `app/`: `test_rpc_surface.py` (`test_every_rpc_method_has_a_ui_caller_or_a_listed_reason` and `test_the_exception_list_stays_honest`).

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `engines_service.py` | test_databricks_billing, test_databricks_bundles_service, test_databricks_runs_service, test_databricks_sync_service, test_engine_connections_settings, test_engines_service, test_engines_service_edges, test_fabric_spark (+ test_engines_service_mutation) | 150 of 391 | 130 / 20 | **86.7%** | 150 / 0 | **100%** | none |
| `shortcut_service.py` | test_shortcut_center, test_shortcut_fixes, test_shortcut_service_paths, test_state_writes (+ test_fail_closed, test_shortcut_request_envs, test_shortcut_request_retries, test_shortcut_watch, test_typed_errors, test_shortcut_service_mutation) | 150 of 366 | 104 / 46 | **69.3%** | 148 / 2 | **98.7%** | 2 equivalent |
| `xray_service.py` | test_failed_lookups, test_optimize_service, test_paging_complete, test_xray, test_xray_service_edges, test_xray_staging (+ test_audit, test_estate_service_edges, test_optimize_outbox, test_risk, test_xray_edges, test_xray_service_mutation) | 150 of 298 | 108 / 42 | **72.0%** | 150 / 0 | **100%** | none |

The 3 timeouts, all in `engines_service.py`, are real hangs: two make `_new_id`'s loop never end, and one makes the loop that picks a free connection id never end.

Existing test files the first sets left out were tried on the survivors first:
- `engines_service.py`: the first set already held every test file that calls it.
- `shortcut_service.py`: test_fail_closed, test_shortcut_request_envs, test_shortcut_request_retries, test_shortcut_watch, test_typed_errors. They killed 15, mostly in shortcut requests: a conflict that is raised again, a waiting request found by its id and kind, the requester's name, and what to do next when the shortcut was already there.
- `xray_service.py`: test_audit, test_estate_service_edges, test_optimize_outbox, test_risk, test_xray_edges. They killed 2: what sharing with the central lakehouse returns when the findings were queued and when it failed.

The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **engines_service.py**:
  - A connection in Settings keeping its sign-in method and CLI profile, falling back to the database for Snowflake, and showing as "profile · label".
  - A connection key whose connection part has a colon naming that whole part.
  - A remote engine asking for a confirm unless the profile's policy allows that very engine.
  - Prepare saying the session closes when moving from one local engine to another, and that nothing changes for the same engine or the kernel.
  - The risk check's wording for a stopped warehouse and one with auto-stop off, and for 1 and 6 cells that may change data.
  - A Databricks sync pair that shares only its local or only its remote folder with another being kept, and pair ids of 10 hex characters.
  - A chosen engine whose start was forgotten reading as ready, and a running cell's time to a tenth of a second.
  - Sessions and cells running on daemon threads, so the app can quit.
  - → `test_engines_service_mutation.py`.
- **shortcut_service.py**:
  - The history windows asked of the platform: 30 days and 2,000 changes for the history, 7 days and ten years for the center, ten years for undo.
  - Saving rules returning them, and a repoint across environments returning its warning.
  - A row's first-seen time and its target's environment, and the environments read from the platform's config.
  - The week's count of changes, and environment groups leaving out a layer that is in only one environment.
  - Export making the Downloads folders it needs.
  - Finding a moved target: never listing lakehouses without a name, picking the candidate with more tables, a 300 answer counting as not there, and asking only the first five.
  - Repointing many reporting each warning and problem once.
  - The readers check saying "Matches" when both sides have the same readers.
  - Undo refusing a repoint that recorded no before or after, and an add being undone by its name.
  - A conflicting shortcut being kept on a 400 answer, its message, a shortcut made by hand after the request counting as found, and a twin copy's conflict message.
  - → `test_shortcut_service_mutation.py`.
- **xray_service.py**:
  - A table named with a leading digit getting a letter first, and a table with no id column keyed by its first column.
  - An adopted entity loading new rows by its timestamp column, with "me" as owner when none is known.
  - A dismissed finding's id of up to 1,024 characters and its reason cut at 500.
  - A scan starting unpaused, and pause with no argument pausing it.
  - A scan adding the last 30 days' cost findings, and a second scan writing over the first.
  - Sharing with a central lakehouse that has no name saying "the central lakehouse".
  - Annotating reading the runs log fresh by default, 500 runs.
  - A lakehouse counted as managed or adopted only for its own workspace and tables.
  - A fix that ended in the same second the scan began still counting as fixed.
  - Optimize naming the table with its schema.
  - Vacuum and Optimize assessed under their own label and workload id, without a fresh capacity read.
  - A risk check that can't run being unknown and asking for nothing.
  - A vacuum sized from a recursive listing, and 0 bytes when nothing is listed.
  - Adopt keeping the picked keys, saying which schema and table it came from, and taking the owner from the sign-in.
  - A young staging lakehouse saying how long ago it was written or made, in days.
  - A refresh less than an hour after the last write not showing a staging lakehouse unused.
  - Remove logging the workspace's name and keeping removals from the last 90 days.
  - → `test_xray_service_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `shortcut_service.py` | 98 | `cached is not None and cached is not self._sc_recorded` → `or` | Equivalent: when `all_shortcuts` returns a finished read it always leaves the cache set, so the first test never decides the answer. |
| `shortcut_service.py` | 233 | `zip(shortcuts, hits, strict=True)` → `strict=False` | Equivalent: `hits` is built from `shortcuts` one for one, so the two always have the same length. |
### Real bugs found

- [#2640](https://github.com/kratosdatagod/fabric-desktop/issues/2640): approving a shortcut request counts an "added" change from the same second, just before the request, as the request's own. The request's time drops microseconds and a change's time keeps them, so comparing them as text puts the earlier change after the request.

### Not in this batch

- `engines_service.py`, `shortcut_service.py` and `xray_service.py` past the 150 sampled mutants.
- `meta/store`, `meta/project`, `mailbox`, `cloud_service` and `git/repo`, which batch 14 took.

