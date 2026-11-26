# Mutation testing, batch 54

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- IPython's input and output history in a notebook: `In`, `Out`, the underscores and `%history` (`history.py`).
- Which Fabric item types work with Fabric Git, deployment pipelines and item definitions (`artifacts.py`).
- Workspaces page previews: a table's first rows, a file's start, a notebook's cells (`preview.py`).

The run was at `main` 59968c9b3 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out, and no `-k` filter was used.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing. Two modules were run first and found already at 100% with their existing tests, so they got no new tests: `optimize/local_runs.py` (42 / 42: 41 point + 1 boundary, with test_optimize_service_mutation and test_optimize_local_runs) and `sqlitefile.py` (33 / 33, all point, with test_sqlite_stores, test_sqlite_busy and test_workload_store_faults). `learn/datasets.py` was looked at and left for later, because its tests take nearly two minutes.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `history.py` | test_history_edges, test_notebook_history, test_notebook_magics_windows (+ test_history_mutation) | 75 of 75 (68 + 7) | 67 / 8 | **89.3%** | 63/68 → 66/68 | 4/7 → 6/7 | 72 / 3 | **96.0%** | 3 equivalent |
| `artifacts.py` | test_artifacts, test_fabric_git_service_mutation, test_fabric_git (+ test_artifacts_mutation) | 60 of 60 (60 + 0) | 29 / 31 | **48.3%** | 29/60 → 60/60 | none | 60 / 0 | **100%** | none |
| `preview.py` | test_preview (+ test_preview_mutation) | 75 of 75 (73 + 2) | 59 / 16 | **78.7%** | 59/73 → 69/73 | 0/2 → 2/2 | 71 / 4 | **94.7%** | 4 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **history.py**:
  - `_i`, `_ii` and `_iii` are empty before the first cell, even when `In[0]` holds text, and `_iii` is three cells back.
  - `%reset -f` removes each numbered result, and works when there is no `Out` yet.
  - A range such as `2-3` lists each cell in it.
  - A cell number past the last one is skipped.
  - → `test_history_mutation.py`.
- **artifacts.py**:
  - Which types have no definition API, and which Fabric Git and deployment pipelines mark preview, are pinned as sets. These come from the Microsoft lists in `SOURCES`, so a change to a list means updating the registry and the test together.
  - Only Notebook, DataPipeline and Warehouse round-trip.
  - A capability can't be changed once made.
  - → `test_artifacts_mutation.py`.
- **preview.py**:
  - The limits are 100 rows, 200 MiB and 20,000 characters, and a CSV shows 100 rows by default.
  - Dates and decimals show as text, and NaN as its name.
  - A CSV is read only until it has enough rows: a reader handing out batches of 5 is stopped after the batch that reaches the limit.
  - A text file of exactly the limit is not marked as cut.
  - → `test_preview_mutation.py`.

### Equivalent mutants left

- **history.py**:
  - Line 23, `len(inputs) < n` to `<=`: the extra entry is replaced by `inputs[n:] = [code]`, so `In` ends the same.
  - Line 85, the end of a single-number range read from the whole match instead of its first group: for a single number they are the same text.
  - Line 94, the default range starting at 0 instead of 1: cell 0 is skipped by the `0 < n` check below.
- **preview.py**:
  - Line 45, `zip(..., strict=True)` to `False`: the columns of one table always have the same length.
  - Line 64, `flatten=True` to `False`: with the pinned deltalake, `num_records` is a top-level column either way.
  - Line 65, converting the add actions to a pyarrow table only when they aren't one, negated: the pinned deltalake's table has `.column(...).to_pylist()` too, so the count is the same.
  - Line 74, the file name split from the path with `rsplit("/", 0)`: an extension taken from a folder name always contains `/`, so it matches no known kind.

