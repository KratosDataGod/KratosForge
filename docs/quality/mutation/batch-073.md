# Mutation testing, batch 73

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The decision patterns reader, which turns the decision patterns page into what the app ships (`learn/patterns.py`).
- Which of a notebook's cells a run runs, and waiting for each cell (`notebook_cells.py`).
- The Workspaces page previews: where a previewed file goes on this PC and which files are refused (`preview_service.py`).

The run was at `main` 21f72ad25 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. The test file that checks `learn/patterns.py`, `test_decision_patterns.py`, reads `docs/`, so it was left out; the "before" run for that module used `test_notebook_cells.py`, which doesn't reach it, so its "before" score is 0. The new tests use small pages of their own and don't read `docs/`. `test_state_writes.py`, which lists the module's file write, also reads `docs/` and was left out too.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `learn/patterns.py` | test_notebook_cells (+ test_learn_patterns_mutation) | 21 of 21 (21 + 0) | 0 / 21 | **0%** | 0/21 → 21/21 | none | 21 / 0 | **100%** | none |
| `notebook_cells.py` | test_notebook_cells, test_integrations_executions_mutation, test_pipeline_notebooks_edges, test_replay_edges, test_meaningful_diffs (+ test_notebook_cells_mutation) | 32 of 32 (32 + 0) | 29 / 3 | **90.6%** | 29/32 → 32/32 | none | 32 / 0 | **100%** | none |
| `preview_service.py` | test_preview, test_audit (+ test_preview_service_mutation) | 26 of 26 (25 + 1) | 18 / 8 | **69.2%** | 18/25 → 25/25 | 0/1 → 1/1 | 26 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **learn/patterns.py**:
  - Each `### DP-nn` section gives its title, body, language and example, and a section ends at the next `##` heading or at the end of the page.
  - Only an id of two digits on a line of its own starts a pattern.
  - A section with no bold title line uses its id as the title, and one with no example has an empty language and example. A bold line that isn't first, or has words after it, isn't the title.
  - Only the first fenced block is the example, and a fence with no language keeps its example.
  - A list item wrapped with two spaces joins its line; a deeper indent doesn't. One blank line stays, and two or more become one.
  - `build` reads the page from the folder it's given and sorts by id, `load` reads the shipped file next to the module, and run as a script it writes the patterns next to itself, sorted, with a one-space indent and without escaping non-ASCII text.
  - → `test_learn_patterns_mutation.py`.
- **notebook_cells.py**:
  - The first poll asks from version -1, and a snapshot with no version keeps the last one asked.
  - A cell that finished or was stopped with no outputs gives an empty list, a cell stopped at once keeps the outputs it started with, and a last snapshot with no outputs gives the ones seen before it.
  - → `test_notebook_cells_mutation.py`.
- **preview_service.py**:
  - A previewed file keeps its extension only when it is one to ten letters or digits.
  - A destination outside the preview's own folder is refused.
  - A file exactly at the size limit is read, and one byte over isn't.
  - A path outside `Files/`, or with a `..` step, is refused before anything is downloaded; dots that are part of a name are allowed.
  - Each preview gets a folder of its own, removed when the preview is read, when reading fails, and when the download stops at the limit. A download that fails before its folder exists still says why.
  - A table preview attaches the lakehouse before reading the table, and a notebook preview asks for the notebook's `ipynb` definition.
  - → `test_preview_service_mutation.py`.

### Left

None.

