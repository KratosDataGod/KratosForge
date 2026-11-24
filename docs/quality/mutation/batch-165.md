# Mutation testing, batch 165

A boundary re-score, as in batch 163, of six more modules first scored before batch 36. Only the boundary mutants were run (`--kinds boundary`); each module's point score from its first batch stands. The modules, with the batch that first scored each:
- OneLake shortcuts: repointing several at once, and whether a requested shortcut was made (`shortcut_service.py`, batch 15).
- The Estate X-ray's leftover Dataflow Gen2 staging lakehouses: the checks before one is removed (`xray_service.py`, batch 15).
- Runs that external tools start: output sizes, SQL results and stopping a run (`integrations/executions.py`, batch 23).
- Side by side runs (`side_by_side_service.py`, batch 24).
- The local HTTP server for external tools: audit lines for refused requests and the parent process it watches (`integrations/http_server.py`, batch 26).
- Schema drift: which type changes widen (`meta/drift.py`, batch 29).

The run was at `main` 4ddd3662 (2026-11-18) with batches 163 and 164's tests added, and the same method as batch 163: every test file that uses the module, as paths relative to `sidecar/`, first passing once on the unmutated copy, and every boundary mutant of each module. Tests that read files outside `sidecar/` were left out: `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` for `side_by_side_service.py` and `test_integrations_conformance.py::test_capability_ids_in_docs_are_real` for `integrations/http_server.py`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `shortcut_service.py` | test_shortcut_center, test_shortcut_fixes, test_shortcut_service_mutation, test_shortcut_service_paths (+ test_shortcut_service_boundary) | 6 of 6 | 4 / 2 | **66.7%** | 6 / 0 | **100%** | none |
| `xray_service.py` | the 6 test files that use it (+ test_xray_service_boundary) | 6 of 6 | 2 / 4 | **33.3%** | 6 / 0 | **100%** | none |
| `integrations/executions.py` | the 5 test files that use it (+ test_integrations_executions_boundary) | 6 of 6 | 1 / 5 | **16.7%** | 4 / 2 | **66.7%** | 2 timing only |
| `side_by_side_service.py` | test_side_by_side, test_side_by_side_edges, test_side_by_side_service_mutation, test_table_uris | 6 of 6 | 6 / 0 | **100%** | 6 / 0 | **100%** | none |
| `integrations/http_server.py` | the 10 test files that use it (+ test_integrations_http_boundary) | 6 of 6 | 4 / 2 | **66.7%** | 6 / 0 | **100%** | none |
| `meta/drift.py` | test_meta_drift_edges, test_meta_drift_mutation, test_schema_evolution (+ test_meta_drift_boundary) | 6 of 6 | 4 / 2 | **66.7%** | 5 / 1 | **83.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **shortcut_service.py**: repointing one shortcut adds no note about others, and two name each other; a shortcut added at exactly the request's time is the request's own. → `test_shortcut_service_boundary.py`.
- **xray_service.py**: nothing written for exactly the quiet days is quiet enough; a refresh exactly the margin after the last write shows the dataflow doesn't stage there; Power BI answering exactly 400 is an error; a removal noted exactly 90 days ago is kept. → `test_xray_service_boundary.py`.
- **integrations/executions.py**: an output of exactly the budget left is sent; a result of exactly `max_rows` isn't truncated; interrupting a query stops at exactly its deadline. → `test_integrations_executions_boundary.py`.
- **integrations/http_server.py**: a refused request exactly `REFUSED_EVERY` seconds after the last line gets a line of its own; a parent process id of 0 means no parent to watch. → `test_integrations_http_boundary.py`.
- **meta/drift.py**: an unsigned integer to a signed one of the same width doesn't widen. → `test_meta_drift_boundary.py`.

`side_by_side_service.py` had no survivors.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `integrations/executions.py` | 280 | `time.monotonic() >= deadline` to `>` while a run is waited for | Timing only: at exactly the deadline the run is returned one 0.05 s poll later, as it stands then. |
| `integrations/executions.py` | 383 | `time.monotonic() < deadline` to `<=` while a cancelled run's kernel stops | Timing only: at exactly the deadline the kernel is ended one 0.1 s poll later. |
| `meta/drift.py` | 92 | `end > start + 1` to `>=` | Equivalent: at `start + 1` the line looked at is the section's header, which isn't blank, so the loop stops there either way. |

