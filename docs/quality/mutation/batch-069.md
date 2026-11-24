# Mutation testing, batch 69

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- OneLake > Access across every workspace as the app calls it: read the cache, refresh it, and choose who may read it (`access_cache_service.py`).
- The notebook explorer's calls: list, add, rename, delete and Show in File Explorer (`notebook_resources_service.py`).
- The data dictionary card on the Semantic model tab: read, export, save, preview and import (`model_dictionary_service.py`).

The run was at `main` 996b6ee54 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_support_bundle_save.py` reaches `notebook_resources_service.py` but reads `app/`, and `test_state_writes.py` reaches `model_dictionary_service.py` but reads `docs/`, so both were left out, which can only lower the "before" scores.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `access_cache_service.py` | test_access_cache, test_access_cache_service_edges (+ test_access_cache_service_mutation) | 54 of 54 (54 + 0) | 48 / 6 | **88.9%** | 48/54 → 54/54 | none | 54 / 0 | **100%** | none |
| `notebook_resources_service.py` | test_notebook_resources, test_notebook_resources_calls (+ test_notebook_resources_service_mutation) | 25 of 25 (25 + 0) | 22 / 3 | **88.0%** | 22/25 → 25/25 | none | 25 / 0 | **100%** | none |
| `model_dictionary_service.py` | test_model_dictionary, test_model_dictionary_mutation, test_model_dictionary_service_edges (+ test_model_dictionary_service_mutation) | 22 of 22 (22 + 0) | 21 / 1 | **95.5%** | 21/22 → 22/22 | none | 22 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **access_cache_service.py**:
  - The cache names each workspace and lakehouse as Fabric does, and a lakehouse with no name is kept with an empty one.
  - A connection that doesn't say otherwise reads the lakehouse with schemas.
  - Before and after the tables are set up, the cache says this PC may write only when its connection is for writing.
  - Choosing no one, or only blanks, is refused even when confirming, and nothing is written.
  - → `test_access_cache_service_mutation.py`.
- **notebook_resources_service.py**:
  - Showing the folder a second time shows the same folder.
  - The program that shows a file starts with no flags where there is no no-window flag, and with it where there is one.
  - Handles are closed for the program except on Windows.
  - → `test_notebook_resources_service_mutation.py`.
- **model_dictionary_service.py**:
  - When a save fails part way, a column is named with its table, and a table on its own.
  - → `test_model_dictionary_service_mutation.py`.

### Left

None.

