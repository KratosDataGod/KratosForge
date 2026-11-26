# Mutation testing, batch 157

A boundary re-score of two modules first scored in batches 7 and 8, before `mutate.py` made boundary mutants (batch 36), run again with `--kinds boundary` only:
- The PySpark calls on this PC: the typed-column cache, split with a limit of 0, frame bounds, Java's way of writing a double, format_number, show's widths, star prefixes, describe's running stddev, intervals, locate and drop (`spark_compat.py`, batch 8).
- Spark SQL translated for DuckDB: JSON paths and JSON types at their limits, bround, integer ranges, range(), from_json's decimals, an unfinished STRUCT< and head() (`sql.py`, batch 7).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran. For `sql.py` the run used batch 7's named test files. For `spark_compat.py` batch 8 ran 28 files, about 90 s a run, so this run used the ten that call the module most directly: test_spark_compat, test_spark_compat_edges, test_spark_compat_mutation, test_spark_local, test_spark_show, test_spark_column_methods, test_spark_stat_methods, test_spark_df_extras, test_spark_dataframe_decimals and test_spark_integer_casts. `test_spark_compat.py::test_these_frames_leave_no_view_behind` (about 30 s, and it checks no edge) was left out with `--deselect`. A mutant the other files kill may show as a survivor here.

The new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_compat.py` | the ten files above (+ test_spark_compat_boundary) | 38 of 38 | 10 / 28 | **26.3%** | 30 / 8 | **78.9%** | 6 equivalent, 2 out of Spark's reach |
| `sql.py` | test_sql_compat, test_sql_editor, test_sql_run_edges, test_bounded_previews, test_untranslatable_sql, test_tsql_contract, test_shell_and_decimals_edges, test_safe_parse_readers, test_time_travel, test_duckdb_try_cast, test_sql_lambdas, test_sql_mutation (+ test_sql_boundary) | 17 of 17 | 1 / 16 | **5.9%** | 13 / 4 | **76.5%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_compat.py**:
  - Exactly `_TYPED_KEPT` typed columns are kept. Split with a limit of 0 keeps every part, with a pattern and with an empty one.
  - A frame bound of exactly `unboundedFollowing` is unbounded. A nested type at depth 0 adds no lines.
  - A double is written plainly from 10^-3 up to, not including, 10^7, as Java writes it.
  - format_number with 0 and with 18 places is worked out on exact decimals, not by printf.
  - show with exactly n rows says nothing about more, truncates at exactly its width, and cuts a width of 4 or less without "...".
  - A star after one name is that name, and one after an empty first name isn't a prefix.
  - describe on exactly 100,000 rows takes Spark's running (Welford) stddev, checked against the same update in Python on data where it differs from `stddev_samp`.
  - A zero interval has no sign. locate from position 1, also of an empty string, and with its text given by name. drop of another frame's column that this frame has once.
  - → `test_spark_compat_boundary.py`.
- **sql.py**:
  - A JSON path is read to its end. bround to 0 places keeps a decimal a decimal.
  - A whole-number literal at the top of INT is in range, and a cast to the same integer width can't be past it.
  - JSON numbers of exactly 38 digits, merged decimals of exactly 38 digits, a whole number with prefersDecimal, and whole numbers at both ends of BIGINT.
  - range() with one and with four arguments, its column named `id`. A JSON path with one wildcard. from_json to a decimal with only a precision.
  - A query that ends at `STRUCT<` is read with no index error. head() of exactly its limit.
  - → `test_sql_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_compat.py` | 717 | `count > 0` to `>=` in substring_index | Equivalent: a count of 0 is answered earlier. |
| `spark_compat.py` | 886 | `n < 0` to `<=` in a frame bound | Equivalent: a bound of 0 is CURRENT ROW, answered earlier. |
| `spark_compat.py` | 1540 | `end < 0` to `<=` for a backquoted name | Equivalent: `find` starts after the backquote, so it is never 0. |
| `spark_compat.py` | 1874 | `len(parts) > 1` to `>=` in drop | Equivalent: a one-part name tried with `EXCLUDE` first is then dropped the same way. |
| `spark_compat.py` | 2150 | `level > 0` to `>=` in printSchema | Equivalent: a level of 0 is falsy, so `level and ...` stops first. |
| `spark_compat.py` | 2215 | `value > 0` to `>=` for an infinity | Equivalent: only infinities get there, and neither is 0. |
| `spark_compat.py` | 1561, 1867 | `len(whole) > 1` to `>=` for a name of one backquote | Out of Spark's reach: Spark, and DuckDB 2.0's Spark API, refuse `` col("`") `` as a name; only DuckDB 1.5.6 takes it. |
| `sql.py` | 444 | `places >= 0` to `>` in bround | Equivalent: at 0 places the value is only scaled by 1 for the tie check, and the answer comes from TRUNC or ROUND of the value itself. |
| `sql.py` | 1439 | `-(2 ** (bits - 1)) <= int(...)` to `<` | Equivalent: a literal is never negative (sqlglot reads -5 as a minus sign before 5). |
| `sql.py` | 2017 | `n < 0` to `<=` for a slice's length | Equivalent: the check only skips writing the guard for a literal; the guard written instead tests `< 0` itself. |
| `sql.py` | 2392 | `have < limit` to `<=` in head() | Equivalent: at the limit, the slice kept is empty. |

