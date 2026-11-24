# Mutation testing, batch 12

Four larger modules no batch had covered yet, where no open pull request is changing the module:
- The local pipeline runner (`orchestration/runner.py`).
- A metadata project's data mesh files: products, links, the save gate's mesh rules and the promote rule (`meta/mesh.py`).
- Reading and writing TMDL on this PC (`tmdl.py`).
- Schedules on this PC and Fabric's schedules in one list (`schedule_service.py`).

`orchestration/evaluate.py` was left out because an open pull request changes it. `progress.py`, the next candidate, was left out too: it is the developer profile behind ranks and achievements, next to the academy area this program skips. `schedule_service.py` took its place.

The run was at `main` 4d7f4b6bd (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 10 (sample cap 150, seed 7), on DuckDB 1.5.6. Every module has more points than the cap, so 150 of each were run. Before each run, its tests were run in a copy of `sidecar/` on its own, the way the mutator runs them.

Tests left out of every run:
- `test_pipeline_coverage.py::test_the_page_is_up_to_date` and `::test_the_page_is_in_the_docs_site`, which read `docs/`.
- For `runner.py`, the per-mutant set was kept to the 16 files that run in about 40 seconds, under the mutator's 120-second limit. The rest of the pipeline tests (`test_pipeline_notebooks`, the expression, date, encoding and time zone files, `test_pipeline_coverage`) were then run against the survivors, and killed one.
- For `meta/mesh.py`, `test_platform.py` (about 50 seconds on its own) was likewise run only against the survivors, with the other mesh files below.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/runner.py` | test_pipeline_runs, _script, _data, _schedules, _service_edges, _definitions, _gotchas, _data_edges, _runner_edges, _notebooks_edges, _script_edges, _fabric_parity, _adapters_edges, test_orchestration_definition_edges, _runs_edges, test_cli_tenant_commands (+ existing test_pipeline_notebooks and the expression files; + test_orchestration_runner_mutation) | 150 of 411 | 124 / 26 (9 timeouts) | **82.7%** | 142 / 8 | **94.7%** | 8 equivalent |
| `meta/mesh.py` | test_meta_mesh_edges, test_breaking_changes, test_change_window, test_versioned_contracts, test_product_promises, test_product_status, test_meta_atomic_save (+ existing test_platform, test_mesh_home, test_platform_service_paths, test_schema_evolution, test_git_scopes; + test_meta_mesh_mutation) | 150 of 413 | 127 / 23 | **84.7%** | 148 / 2 | **98.7%** | 2 equivalent |
| `tmdl.py` | test_tmdl, test_tmdl_edges, test_tmdl_write, test_model_ossie_service_edges (+ test_tmdl_mutation) | 150 of 484 | 124 / 26 (2 timeouts) | **82.7%** | 145 / 5 | **96.7%** | 5 equivalent |
| `schedule_service.py` | test_schedules, test_schedule_locks_and_lists, test_schedule_service_edges, test_signed_out_schedules, test_fabric_pipeline_runs, test_fabric_connections (+ existing test_notebook_runs, test_notebook_batch, _edges, _stop, test_pipeline_schedules, test_cli_local_commands, test_integrations_scope_edges, test_integrations_workspace_scope, test_risk; + test_schedule_service_mutation) | 150 of 369 | 83 / 67 | **55.3%** | 150 / 0 | **100%** | none |

How the survivors were killed:
- `schedule_service.py`: 36 of the 67 by existing files the first set left out (a notebook's runs panel, Home's batch runs, pipeline schedules and the CLI), and 31 by the new tests.
- `meta/mesh.py`: 3 of the 23 by existing files (`test_platform` and the others above), 18 by the new tests.
- `orchestration/runner.py`: 1 of the 26 by the rest of the pipeline tests, 17 by the new tests.
- `tmdl.py`: the existing model files (`test_model_date_table`, `_dictionary`, `_directlake`, `test_model_ossie`) killed none; the new tests killed 21.

The runner's 9 timeouts and tmdl's 2 are real hangs: run again with nothing else on the machine, each still passed the 120-second limit, while the runner's set takes about 40 seconds and tmdl's under 5. Examples are a run loop whose wait never ends (`runner.py` 392, 411, 457, 501) and TMDL's file-name loop or reader that never moves on (`tmdl.py` 563, 198).

All four new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **orchestration/runner.py**:
  - A Boolean variable declared without a default starts false.
  - A mock without `seconds` doesn't wait, and parallel work is capped at 32 (and at least 1).
  - Activity ids come from the run id and the activity's path.
  - A skipped activity has no start time; the record's counts add up by state; seconds keep three decimals.
  - The note that Lookup, Get Metadata and Script steps read OneLake appears only when one ran here.
  - A container (If Condition) isn't retried as a whole; a policy timeout counts from the activity's start.
  - A failed mock keeps the output it was given, and one without has none.
  - A notebook step's own failure is its error, not a LocalAdapterError.
  - A run cancelled from outside says `cancelled` and starts nothing, not even a mock that would end at once.
  - A pipeline that runs itself is refused as too deep at check time.
  - AppendVariable keeps a running size of the array.
  - → `test_orchestration_runner_mutation.py`.
- **meta/mesh.py**:
  - The project file's `format = 2`; a link file's `[reads]` and only real pinned environments.
  - A long name that doesn't match its file is cut to 80 characters in the error.
  - A link's summary and pinned tables; a product with no version is version 1.
  - Landing readers grouped by source; the starter catalog read only from a table; a column's saved classification winning over the starter one.
  - Known workspace designations pass and unknown ones don't; a catalog without a classification and a link that exists pass.
  - A promise of 0 % rejected rows is allowed, and a promise is listed once per product for each entity.
  - A status with more than two reasons says "and N more".
  - A deprecation date with a time is read by its date, and the advice to widen accepts keeps the reader's lowest version.
  - → `test_meta_mesh_mutation.py`.
- **tmdl.py**:
  - The exact TMDL the writer lays out: a fenced description with edge spaces, a calculated column without its type, a column of another type with an expression, a format string definition and its own properties, a partition's source expression, compact JSON for an extended property, top-level relationships with `Table.Column` ends, an M expression without its default kind.
  - A reader's note (cultures) reaching the report `from_bim` returns; a bad property name cut to 80 characters; the 20 MB limit on writing checked before reading back.
  - Files read as database, model, expressions, then tables, then relationships, then the rest.
  - A relationship with one end missing still reads; a format string definition ends where its siblings begin.
  - `column_ref`'s 1,024-character limit and its bad forms; `unquote` and values need both quotes.
  - → `test_tmdl_mutation.py`.
- **schedule_service.py**:
  - What each call returns: a deleted schedule's id, Fabric's answer to a change, an empty text when no alert needs recording (no webhook, a run that worked, an alert that went out).
  - A changed schedule passes `signed_out` only when given; 200 runs in the history and 50 in a notebook's panel unless asked.
  - A one-cell notebook says "1 cell ran".
  - A pipeline schedule checks and starts with empty settings left out (None, not `{}` or ""), refuses a pipeline that can't run here or can't be read, and names one SecureString as one.
  - A scheduled pipeline run followed to its end, and a failed one naming its failed activity.
  - Only signed-out schedules skipped when IT turned them off, with 0 seconds.
  - Another kind of schedule with the notebook's name isn't the notebook's; Fabric run seconds keep one decimal; both Fabric reads ask for 500 runs.
  - A lock older than 12 hours taken over even from a live process; the alerts lock left by a dead run taken.
  - A stopped run's pid kept only when it is a whole number; checking a process sends it no signal.
  - An ended process is `False`, pid 1 with another start time has ended, and Windows' process times ask for all four times and read the last error.
  - Run now on Fabric uses Data Factory only for its item types; a notebook's publish workspace listed and one without none; a Fabric schedule without `enabled` is off.
  - → `test_schedule_service_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `runner.py` | 561 | `_matches`' last `return False` → `None` | Its callers only test it for truth. |
| `runner.py` | 660 | `or` → `and` in ForEach's check before starting an iteration | An iteration started after a cancel raises in `run_list` before it records anything, so nothing seen changes. |
| `runner.py` | 710 | a child run's `depth + 1` → `depth - 1` | Nesting is refused at check time by the top-level run, and depth is otherwise only compared with 0, which a child never is either way. |
| `runner.py` | 749 | `_watch_child`'s time left computed with `+` | The wait is capped at 0.2 seconds either way, so the deadline check keeps the same pace. |
| `runner.py` | 450 | `getattr(adapter, "container", False)` → `True` | Every adapter in the registry sets `container`, so the default is never used. |
| `runner.py` | 408, 664 | `daemon=True` → `False` on activity and ForEach threads | Each is joined before the run ends; the flag only matters at interpreter exit. |
| `runner.py` | 768 | `child.id[:8]` → `[:9]` in a thread's name | Only the thread's name changes. |
| `mesh.py` | 172 | `_incident_touches`' `return False` → `None` | Its callers only test it for truth. |
| `mesh.py` | 323 | `identity`'s `format` default 1 → 0 | `0 or 1` is still 1. |
| `tmdl.py` | 122 | the `if not dot: raise` removed in `column_ref` | The column part is then empty, and the next check raises the same error. |
| `tmdl.py` | 342, 345 | `_file_order`'s ranks 10 → 11 and 30 → 31 | The order of the files is the same. |
| `tmdl.py` | 169 | a fenced expression's `default=0` → `1` | Only used when the body has no line with text, and then every line is blank either way. |
| `tmdl.py` | 246 | `m.group(1)` → `m.group(0)` for `^(\w+)$` | The whole match is the group. |

### Real bugs found

None.

### Not in this batch

- `orchestration/evaluate.py`, which an open pull request changes.
- `progress.py`, the developer profile, next to the academy area.

