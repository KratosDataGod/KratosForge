# Mutation testing, batch 68

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Which Databricks notebook helpers (`dbutils` calls and `%` magics) run on this PC, and the page that lists them (`databricks_notebook_helpers.py`).
- A notebook's version history as the app calls it: list, open, compare, checkpoint, restore and delete (`notebook_versions_service.py`).
- Saved and shared queries for the Warehouse page (`sql_queries_service.py`).

The run was at `main` 9091dd082 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. The only test file that reaches `databricks_notebook_helpers.py`, `test_databricks_notebook_helpers.py`, compares the committed `docs/databricks-notebook-helpers.md`, so it was left out. Its "before" run used a test file that only imports the module, so every mutant survived it; the new tests don't read `docs/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_notebook_helpers.py` | an import-only file (+ test_databricks_notebook_helpers_mutation) | 44 of 44 (44 + 0) | 0 / 44 | **0%** | 0/44 → 44/44 | none | 44 / 0 | **100%** | none |
| `notebook_versions_service.py` | test_notebook_versions_service_edges, test_notebook_versions, test_notebook_folders (+ test_notebook_versions_service_mutation) | 28 of 28 (28 + 0) | 24 / 4 | **85.7%** | 24/28 → 28/28 | none | 28 / 0 | **100%** | none |
| `sql_queries_service.py` | test_sql_queries, test_sql_queries_edges (+ test_sql_queries_mutation) | 76 of 76 (70 + 6) | 62 / 14 | **81.6%** | 62/70 → 70/70 | 0/6 → 6/6 | 76 / 0 | **100%** | none |

One `sql_queries_service.py` mutant (`i += 1` to `i += 0` in the header loop) never ends, so it is stopped at the timeout and counts as killed, before and after.

No bugs were found.

### What the survivors were, and the tests that now kill them

- **databricks_notebook_helpers.py**:
  - The stand-in notebookutils raises on every call, so a probe never does real work.
  - The walk over `dbutils` goes into namespaces and widgets, skips private names and `help`, and leaves out `bind` and `describe` only on widgets.
  - A probe finds a missing part at any depth, reports a refusal with its message, counts any other error as running, and prints nothing.
  - Each call, cell magic and line magic gets its status and note; Scala and R cells are refused.
  - The page counts each status per group, lists every helper with its prefix, turns `|` in a note into `/`, and ends with the source.
  - The list file is read from beside the module, and importing the module writes nothing.
  - → `test_databricks_notebook_helpers_mutation.py`.
- **notebook_versions_service.py**:
  - A version and a compare read back their label, or an empty one.
  - Unsaved cells keep their compute, and none means the default.
  - A delete names the version it deleted.
  - → `test_notebook_versions_service_mutation.py`.
- **sql_queries_service.py**:
  - A file of only header lines has no SQL; one line of spaces after the header is the gap and is dropped, a second one stays.
  - A good query has an empty problem; a name, warehouse or description exactly at its limit is taken.
  - The fingerprint is the first 16 hex digits of SHA-256.
  - A query exactly at the size limit is saved and one byte more is refused, and a listed file exactly at the limit is read while a larger one says it is larger than 64 KB.
  - → `test_sql_queries_mutation.py`.

### Left

None.

