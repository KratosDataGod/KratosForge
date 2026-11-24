# Mutation testing, batch 85

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The Python engine's session on this PC: its own kernel process behind the engine contract (`engines/local_python.py`).
- Notebook steps of a local pipeline run, in a fresh kernel with the proof runtime bound (`pipeline_notebooks.py`).
- The DataFrame methods added for Fabric notebook parity: `freqItems`, `inputFiles`, `to`, `withMetadata` and `writeTo` (`spark_df_more.py`).

The run was at `main` 92c08926f (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

The first `spark_df_more.py` run used only `test_spark_df_more` and `test_spark_api_mutation`. Three more test files also call the module (`test_spark_api_fails_cleared`, `test_prove_spark`, `test_prove_spark_install`), so it was run again with them, and the "before" figures below are from that run.

A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/local_python.py` | test_engines, test_engine_registry_mutation (+ test_local_python_mutation) | 50 of 50 (49 + 1) | 46 / 4 | **92.0%** | 45/49 → 49/49 | 1/1 → 1/1 | 50 / 0 | **100%** | none |
| `pipeline_notebooks.py` | test_pipeline_notebooks, test_pipeline_notebooks_edges, test_pipeline_script, test_fabric_step, test_notebook_cells, test_pipeline_data (+ test_pipeline_notebooks_mutation) | 140 of 140 (138 + 2) | 122 / 18 | **87.1%** | 121/138 → 138/138 | 1/2 → 2/2 | 140 / 0 | **100%** | none |
| `spark_df_more.py` | test_spark_df_more, test_spark_api_mutation, test_spark_api_fails_cleared, test_prove_spark, test_prove_spark_install (+ test_spark_df_more_mutation) | 109 of 109 (107 + 2) | 76 / 33 | **69.7%** | 76/107 → 106/107 | 0/2 → 2/2 | 108 / 1 | **99.1%** | 1 equivalent |

One bug was found, in `spark_df_more.py`, and is fixed; its test in `test_spark_df_more_mutation.py` now passes:
- `inputFiles` left out a file when any part of its path contains `_delta_log`, not only the Delta log folder itself. So a plain folder named, for example, `orders_delta_log_archive` listed no files. It now skips only the `_delta_log` folder inside the table.

### What the survivors were, and the tests that now kill them

- **engines/local_python.py**:
  - A kernel's first reply with no version is polled from -1, and one with a version is polled from it.
  - A cell's seconds are the time between its start and its end.
  - The engine has no catalog, and says to use the Data panel instead.
  - The process adapter describes the Python engine and makes its session's kernel with the factory it was given.
  - → `test_local_python_mutation.py`.
- **pipeline_notebooks.py**:
  - Only text names map activities to notebooks.
  - Two notebook steps run at once, and a third waits.
  - A cell that isn't contained needs `allow_uncontained`, which is off unless asked for.
  - A step reports its seconds to the millisecond.
  - Stopping a run stops only the kernels still alive.
  - The notes:
    - A run with no steps and no lakehouse has only the notes added after it.
    - A count of steps reads "1 notebook step" or "2 notebook steps".
    - A lakehouse is named by its name, or by its id when it has none.
  - An unmapped step names the notebook id it runs, or says it has none.
  - A cell with no deadline never times out, and one with a deadline times out only once the deadline has passed.
  - A failed cell names the error and its value, or the cell's status when there is no error output. Its message is cut at 2,000 characters.
  - A step on a lakehouse binds the proof runtime with the run's id, the pinned versions and seed 0, and reports the tables it wrote and the run's folder.
  - → `test_pipeline_notebooks_mutation.py`.
- **spark_df_more.py**:
  - `freqItems` takes one name or a non-empty list of names.
  - Its `support` runs from 1e-4 to 1, both ends included.
  - It quotes a column name with a double quote in it.
  - It replaces a view left from before.
  - `inputFiles`:
    - A plan DuckDB can't write as SQL reads no files.
    - A folder lists its files in every subfolder and never a folder.
    - A Delta folder lists only the files its log names. Remote file URIs are kept as they are, and a relative one is joined to the folder.
    - An unreadable Delta log lists the data files and not the log.
    - A glob lists what it matches, and `**` reaches any depth.
    - A path scanned twice is listed once, with its quote unescaped.
  - `to` takes only a StructType or a DDL string, and `withMetadata` takes only a dict.
  - `select` keeps alias metadata when the columns come as a list.
  - A sort by a column nothing has raises DuckDB's error.
  - The select and sort wrappers are installed once, keep the docs, and leave the schema property as it was when installed again.
  - `writeTo` chains through `tableProperty`, `partitionedBy`, `clusterBy` and `options`, and says what each ignored setting does.
  - → `test_spark_df_more_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_df_more.py` | 67 | `m.group(1)` to `m.group(0)` in `inputFiles` | Equivalent: the whole match adds only the scan's name and its opening bracket, which hold no quoted text, so the quoted paths found are the same. |

