# Mutation testing, batch 240

A re-score of `spark_compat.py`, which batch 8 left at **94.0%**, on today's `main`. Batch 8 ran before boundary mutants, and the module has grown since then, from typed `sequence`, `size` and `posexplode` to window names, text comparisons and drop. So this run uses `--kinds all` and a new sample. Batch 157 has already re-scored its boundary mutants alone.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module now has 1,428 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`.

Batch 8's set less three files: test_spark_builtins_sql, test_spark_sql_names and test_spark_text_casts. They test other modules and took about 170 s of the set's 400 s. The "after" run put them back, and they killed none of the 19 survivors. The module's own targeted files were put first, so a killed mutant stops early. The machine was shared with other mutation runs, so each mutant's timeout was raised from 120 s to 600 s (`mutate.TIMEOUT`); no run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_compat.py` | test_spark_compat_mutation, test_spark_compat_boundary, test_spark_compat_cov3, test_spark_compat_edges, test_spark_local, test_spark_bad_dates, test_spark_dataframe_decimals, test_spark_integer_casts, test_spark_join_drop, test_spark_select_star, test_safe_parse_readers, test_untranslatable_sql, test_spark_show, test_spark_column_methods, test_spark_column_names, test_spark_cube_rollup, test_spark_format_string, test_spark_missing_functions, test_spark_nan_rows, test_spark_summary_schema, test_spark_function_names, test_spark_stat_methods, test_spark_struct_fields, test_spark_divide, test_spark_df_extras, test_pyspark_sql_names, test_spark_cast_names, test_spark_compat (+ test_spark_compat_rescore; the "after" run also has test_spark_builtins_sql, test_spark_sql_names and test_spark_text_casts) | 150 of 1,428 (147 + 3) | 131 / 19 | **87.3%** | 128/147 → 140/147 | 3/3 → 3/3 | 143 / 7 | **95.3%** | 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_compat.py**:
  - `sequence` over int columns is an array of ints, with a step (`[1, 3, 5]`) and without one, counting down (`[5, 4, 3, 2, 1]`).
  - `randn` with a seed gives the same numbers each run, and `randn()` with none runs.
  - `posexplode` puts `pos` first, before `col`, or before `key` and `value` for a map.
  - `row_number` over a window with no ORDER BY stops, as in Spark.
  - A window's frame is written into the column's name (`ROWS BETWEEN -1 FOLLOWING AND 1 FOLLOWING`).
  - `size` of a map keeps the column's alias through the typed rewrite.
  - Text compared with a number keeps its name, `(s = 1)`, or its alias, and its value.
  - Dropping a frame's only column by its Column is refused by name, not left to DuckDB.
  - `x.*` of a DataFrame alias is the frame's columns, and `s.*` of a struct is its fields.
  - A float is written as Java writes it: `0.0`, `-0.0`, `1.0`, `0.1`.
  - → `test_spark_compat_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_compat.py` | 806 | `rand(seed)`'s salt `0` to `1` | Equivalent: the seeded numbers stay in [0, 1) and the same each run, which is all `rand(seed)` promises (they aren't Spark's numbers either way). |
| `spark_compat.py` | 1383 | `zip(..., strict=True)` to `False` | Equivalent: `spark_decimal_columns` gives one answer for each text it is given. |
| `spark_compat.py` | 1579 | `len(star) == 1` to `!=` in `_star_expr` | Equivalent: only the first try is skipped. The code below it resolves a one-part `x.*` the same way, as an alias first and then as a struct. |
| `spark_compat.py` | 1707 | `dropna` with no columns returns nothing | Equivalent: `cols` is the frame's columns or names that `_known` found, and a frame here always has a column (DuckDB can't make an empty struct). |
| `spark_compat.py` | 1887 | `len(parts) > 1` to `> 0` in drop | Equivalent, as batch 157 found for `>=` (its line 1874): a one-part name tried with `EXCLUDE` first is then dropped the same way. |
| `spark_compat.py` | 2253 | `value == 0` to `value == 1` in `_java_float` | Equivalent: 0.0 and -0.0 then go through the digit search, and 1.0 through `_java_double`, and both give the same text (`0.0`, `-0.0`, `1.0`, checked). |
| `spark_compat.py` | 2256 | the digit search's `range(0, 9)` to `range(0, 10)` | Equivalent: 8 places (9 significant digits) always read back as the same 32-bit float, so the 9th place is never reached. |

