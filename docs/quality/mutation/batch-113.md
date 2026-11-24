# Mutation testing, batch 113

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Spark's aggregate results on DuckDB: empty arrays, `count` of several columns, `WITH ROLLUP` and `WITH CUBE`, and the result types of percentiles, `grouping` and `count_if` (`spark_aggregates.py`).
- Spark's window functions on this PC: the int ranks, and the windows Spark stops on without an order or with a frame it doesn't allow (`spark_windows.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_spark_aggregates.py::test_docs_list_what_still_differs` reads `docs/`, so it was left out with `--deselect`.

Every mutant of both modules was killed by the tests already there, so no test was added and there was no "after" run. Neither module has a comparison that a boundary mutant can move. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_aggregates.py` | test_spark_aggregates, test_spark_dataframe_decimals | 30 of 30 (30 + 0) | 30 / 0 | **100%** | 30/30 → 30/30 | none | 30 / 0 | **100%** | none |
| `spark_windows.py` | test_spark_windows | 45 of 45 (45 + 0) | 45 / 0 | **100%** | 45/45 → 45/45 | none | 45 / 0 | **100%** | none |

No bugs were found.

