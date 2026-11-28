# Mutation testing, batch 235

A re-score of `spark_sql_params.py`, which batch 34 left at **98.7%** and batch 36's boundary run at **88.9%**, on today's `main`. Its point and boundary mutants are counted together here (`--kinds all`), so the module has one score below 95% no longer.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module now has 178 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_sql_params.py` | test_spark_sql_params, test_sql_cell_widgets, test_spark_sql_params_mutation | 150 of 178 (141 + 9) | 148 / 2 | **98.7%** | 140/141 → 140/141 | 8/9 → 8/9 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found, and no new tests were needed.

### What the survivors were, and the tests that now kill them

None: both survivors are the equivalent mutants batches 34 and 36 found.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_sql_params.py` | 43 | `value > 0` to `value > 1`, and to `value >= 0` | Equivalent, as in batches 34 and 36: the value there is always an infinity, so the comparisons agree. |

