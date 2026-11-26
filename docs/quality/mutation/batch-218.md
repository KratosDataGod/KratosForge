# Mutation testing, batch 218

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Structured Streaming on this PC: `readStream` sources, the checkpoint, the Delta and file batches, and the table, `foreachBatch`, console, memory and folder sinks (`spark_stream.py`).

Batch 20 sampled 150 of this module's 259 mutants then (85.3% before, 98.0% after), and batch 167 re-scored its 3 boundary mutants (33.3% before, 66.7% after). It now has 268, and this run ran all 268 of 268.

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902 and the method of batch 182: tests first passed on the unmutated copy, and paths were relative to `sidecar/`. Each pytest session got a fresh `--basetemp` folder of its own, from a small run-only plugin that isn't committed, and the plugin removed the folder when the session ended, so the runs don't fill the disk. A fixed per-lane `--basetemp` isn't enough, because pytest deletes it at the start of every session, so a folder nested too deep by one mutant would fail every later session and score each later mutant as killed. Before removing a folder, the plugin checked it for anything nested 40 levels deep, and none was. The last kills of the run don't have near-identical times. Three kills picked at random were checked by hand, as in batch 182: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed a real test (1, 1 and 1).

Every file that uses the module was timed. The set below runs in about 25 seconds on this busy machine. Two slower files were left out of it to keep each mutant's run short: test_academy_pyspark_streaming (about 33 s) and test_csv_cells (about 79 s). The score counts only the files given, as in earlier batches. No `-k` filter or `--deselect` was needed.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways. The new tests pass with DuckDB 1.5.6 and 2.0.0.

One mutant timed out in the full run, and it counts as killed there: swapping `or` for `and` in `_arrow` (line 190), so a relation is read with `.arrow()`. The machine was under load from the other lanes. In the "after" run it was killed by a failing test in 15 seconds, so it is a real kill either way.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_stream.py` | test_file_replace, test_regex_anchors, test_spark_df_extras_edges, test_spark_stream, test_spark_stream_boundary, test_spark_stream_cov3, test_spark_stream_edges, test_spark_stream_mutation, test_startup_imports, test_state_writes (+ test_spark_stream_uncapped) | 268 of 268 (265 + 3), uncapped | 259 / 9 | **96.6%** | 257/265 → 261/265 | 2/3 → 2/3 | 263 / 5 | **98.1%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_stream.py**:
  - A file is known by its path under the stream's folder, or by its own name when the stream reads one file.
  - A memory sink reports the rows it wrote in the query's progress.
  - A stream of a table's change data feed runs in Fabric.
  - A stream's view is named with 12 hex digits.
  - → `test_spark_stream_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_stream.py` | 106 | `history(now - last)` to `history(now + last)` | Equivalent: it asks for more of the latest commits, and only the versions after `last`, which both include, are looked up. |
| `spark_stream.py` | 109 | `range(last + 1, ...)` to `range(last + 0, ...)` | Equivalent: version `last` has the same files as the table already loaded at `last`, so it adds no files and removes none. |
| `spark_stream.py` | 149 | `len(tables) > 1` to `> 0`, and to `>= 1` (boundary) | Equivalent: `pa.concat_tables` of one table gives that table, as batch 167 found. |
| `spark_stream.py` | 343 | `explain`'s default `extended=False` to `True` | Equivalent: `explain` doesn't read the argument. |
