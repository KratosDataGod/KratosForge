# Mutation testing, batch 516

A re-score of `spark_map.py`, which gives this PC's Spark `mapInPandas`, `mapInArrow` and `groupBy().applyInPandas`. Batch 40 scored it at **90.5%**, and batch 236 again at **90.5%** (kinds all), each with 7 equivalent mutants.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged; no mutant's run timed out. Each pytest session got a fresh temporary folder. The module has 74 mutants, so all of them ran. The test set was batch 236's: test_spark_map and test_spark_map_mutation. They first passed once on the unmutated copy, and none of them reads `docs/`, `examples/` or `app/`, so nothing was left out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_map.py` | test_spark_map, test_spark_map_mutation | 74 of 74 (72 + 2) | 67 / 7 | **90.5%** | 65/72 → 65/72 | 2/2 → 2/2 | 67 / 7 | **90.5%** | 7 equivalent |

No bugs were found. No new tests were needed: each survivor is one that batches 40 and 236 found equivalent.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_map.py` | 62, 73 | `barrier=False` to `True` | Equivalent, as batches 40 and 236 said: `barrier` is accepted for Spark's signature and not used on one PC. |
| `spark_map.py` | 70, 132 | `ignore_index=True` to `False` | Equivalent, as batches 40 and 236 said: the joined frame's index isn't used. |
| `spark_map.py` | 90, 91 | `zip(..., strict=True)` to `strict=False` | Equivalent, as batches 40 and 236 said: a batch's columns have the same length. |
| `spark_map.py` | 124 | the early return for an empty frame dropped | Equivalent, as batches 40 and 236 said: an empty frame has no groups, so the same empty result comes back. |
