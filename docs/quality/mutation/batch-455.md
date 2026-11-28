# Mutation testing, batch 455

One module not scored before, run with `--kinds all`:
- Structured Streaming in an Academy PySpark exercise: `spark.readStream` over the exercise's Delta tables and folders of CSV, JSON or Parquet files, and `df.writeStream` to a table, `foreachBatch` or the console, each run taking what arrived since the last one and its checkpoint remembering where it got to (`academy/pyspark_stream.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 288 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_academy_pyspark_streaming and test_file_replace.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests call the reader and writer in the test's own process, with a stand-in for the learner's session (a work folder and a DuckDB connection), so no learner process is started.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pyspark_stream.py` | test_academy_pyspark_streaming, test_file_replace (+ test_academy_pyspark_stream_mutation_b) | 150 of 288 (148 + 2) | 92 / 58 | **61.3%** | 91/148 → 146/148 | 1/2 → 2/2 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pyspark_stream.py**:
  - A flag is true only for True or its text. A quoted name doubles its quotes.
  - A path outside the work folder is refused, and so is a link on the way.
  - A batch already in the stream's columns is kept as it is. Otherwise its columns are cast and missing ones filled, and a column that won't cast is refused.
  - A finished query reports that it stopped, and waiting on it returns at once.
  - The reader's and writer's methods chain. `maxFilesPerTrigger` is noted, and nothing else is.
  - CSV without a schema keeps the one set before, or reads its header. Parquet streams a folder of Parquet files. JSON with a schema casts and fills its columns.
  - An empty folder with a schema takes its columns from the schema. A missing folder with a schema has nothing new.
  - Only files of the format are read. Two new files are one batch. A file's key is its path in the folder.
  - A Delta load must name a table, and a load of a table's path reads that table. A commit after the first run is the next batch, and then there is nothing new.
  - A named query says its name and the rows it wrote, and an unnamed one says "stream". `foreachBatch` has no output row count. The duration is in milliseconds.
  - A processing-time trigger is noted, and `availableNow` isn't. The writer's refusals say why.
  - `start` works with a format or a table's path. Two streams in one query are refused.
  - → `test_academy_pyspark_stream_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/pyspark_stream.py` | 143 | `range(last + 1, ...)` to `range(last + 0, ...)` over a table's new versions | Equivalent: the extra first step reads version `last` itself, whose files are the ones already read, so nothing is removed or added and no rows come from it. |
| `academy/pyspark_stream.py` | 235 | `zip(names, dtype, strict=True)` to `strict=False` for a schema's empty columns | Equivalent: the names and types come from the same StructType, so they always have the same length. |
