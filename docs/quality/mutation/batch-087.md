# Mutation testing, batch 87

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Databricks Asset Bundles: validate, plan and deploy with the person's own Databricks CLI, and the commands a bundle could run on this PC (`engines/databricks_bundles.py`).
- Setting up the central lakehouse: inspecting it, creating and upgrading its tables, and checking access (`optimize/setup.py`).
- Reading and writing the central lakehouse's shared tables: runs, findings, recommendations, local runs and measurements (`optimize/shared.py`).

The run was at `main` 26bde74c1 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `engines/databricks_bundles.py` has 179 mutants, so 150 of them were sampled; the other two modules have fewer than 150, so all were run. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

`orchestration/activities.py` was scored too, with test_activity_registry, test_pipeline_definitions, test_pipeline_gotchas, test_pipeline_runs and the activity-status test of test_pipeline_coverage (the rest of that file reads `docs/`). All 79 of its mutants were killed, so it needed no new tests and isn't in the table.

The first runs of `optimize/setup.py` and `optimize/shared.py` left out some test files that also call them: test_optimize_service_mutation, test_access_cache_service_mutation and test_opportunities_share for `optimize/setup.py`, and test_optimize_shared_edges, test_optimize_service_mutation and test_optimize_local_runs for `optimize/shared.py`. Each survivor of those runs was run again with every calling test file, and the "before" figures below are from those runs.

A mutant whose run times out counts as killed, as in earlier batches. One `optimize/setup.py` mutant timed out in the "before" run (line 95, `missing or not version` to `and`).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/databricks_bundles.py` | test_databricks_bundles, test_databricks_bundles_more_edges, test_databricks_bundles_service (+ test_databricks_bundles_mutation) | 150 of 179 (148 + 2) | 123 / 27 | **82.0%** | 122/148 → 146/148 | 1/2 → 2/2 | 148 / 2 | **98.7%** | 2 equivalent |
| `optimize/setup.py` | test_optimize_setup, test_optimize_shared, test_optimize_service, test_optimize_service_edges, test_optimize_service_mutation, test_access_cache_service_mutation, test_opportunities_share (+ test_optimize_setup_mutation) | 105 of 105 (102 + 3) | 89 / 16 | **84.8%** | 87/102 → 102/102 | 2/3 → 3/3 | 105 / 0 | **100%** | none |
| `optimize/shared.py` | test_optimize_shared, test_optimize_shared_edges, test_optimize_share_analysis, test_optimize_outbox, test_opportunities_share, test_optimize_service, test_optimize_service_edges, test_optimize_service_mutation, test_optimize_local_runs (+ test_optimize_shared_mutation) | 137 of 137 (134 + 3) | 105 / 32 | **76.6%** | 104/134 → 131/134 | 1/3 → 3/3 | 134 / 3 | **97.8%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/databricks_bundles.py**:
  - Validate and plan get a tenth of the deploy time (60 seconds), and deploy gets 600. A CLI that runs out of time says how many minutes it had.
  - The CLI runs with its output captured as text, in the bundle's folder, with no input.
  - A target is production by its mode alone. A target that isn't a mapping has no mode and isn't the default.
  - A command of exactly 300 characters is shown whole, and one of 301 is shortened and counted.
  - The commands digest is 32 characters, and it doesn't depend on the order of keys in the bundle's files.
  - A plan without the `plan` wrapper is read as the rows, and a plan list can name its resources by `key`.
  - A row's kind comes only from a key under `resources` with three parts.
  - The plan digest is 16 characters of the rows with sorted keys.
  - A failure keeps the last 20 lines of the CLI's output, and warnings come from the last 50 lines only.
  - A deploy's log comes from standard output when standard error is empty.
  - → `test_databricks_bundles_mutation.py`.
- **optimize/setup.py**:
  - A text column where a number belongs is a conflict, and only a text column where text belongs is accepted as another string type.
  - A schema version table with no rows, or no table, reads as no versions, and an empty lakehouse has no schema version.
  - Every table with no recorded version is an unfinished setup.
  - A table missing at the latest version is listed both as missing and as one to add.
  - Retry tries five times by default.
  - An upgrade records only the versions it adds and is audited as an upgrade. A first setup is audited as `initialize`.
  - A 401 or 403 means no permission whatever the message, and another status with a plain message doesn't.
  - No read access means no write access either.
  - An error while registering that isn't about permission is raised.
  - → `test_optimize_setup_mutation.py`.
- **optimize/shared.py**:
  - Writes go in batches of 5,000 by default, and a batch is at least one row.
  - A record id is 32 characters, and an item key's workspace is the part before the first slash.
  - Only non-empty text is cleaned of anything credential-shaped.
  - The overhead counters start at nought.
  - Recording local runs or measurements returns how many were sent, and recording none sends nothing.
  - A measurement keeps its period, method, assumptions and reason, and an empty method or reason is stored as no value.
  - Only this installation's runs count as already in the lakehouse.
  - A run without a status is `complete`, and an artifact keeps its item id.
  - A finding keeps its target. Findings of the same severity are most recent first, and findings seen together keep their record order.
  - A recommendation needs a kind. A new one has no state reason until it changes, and a state reason is cut at 500 characters.
  - Recommendations are latest change first, then by id. No recommendations is an empty list.
  - A missing table, by `None`, by "not found" or by an error type named `TableNotFound`, reads as no rows with the table's columns. Any other read error is raised.
  - Rows keep the first of each record, in order.
  - A change made at the same instant as the state it changes is stamped a microsecond later.
  - → `test_optimize_shared_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/databricks_bundles.py` | 118 | `_text(raw, 300)` to `301` for a command shown whole | Equivalent: this branch runs only when the command is at most 300 characters, and `_text` only folds spaces, so the text is never longer than 300 and the limit is never reached. |
| `engines/databricks_bundles.py` | 119 | `_text(raw[:300], 300)` to `301` for a shortened command | Equivalent: the text is already cut to 300 characters before `_text`, so a limit of 301 is never reached. |
| `optimize/shared.py` | 132 | `max(applied, default=0)` to `default=1` before a write | Equivalent: the check refuses only a version above this app's latest, which is at least 1, so 0 and 1 both pass. |
| `optimize/shared.py` | 330 | `maintain_order=True` to `False` when grouping a recommendation's first event | Equivalent: the groups are joined by recommendation id and the result is then sorted by change time and id, which is unique, so the group order never shows. |
| `optimize/shared.py` | 332 | `maintain_order=True` to `False` when grouping a recommendation's latest event | Equivalent: the same final sort by change time and the unique id fixes the order. |

