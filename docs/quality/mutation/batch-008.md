# Mutation testing, batch 8

The local PySpark and Spark SQL layer, which no open pull request is changing:
- The PySpark calls this PC adds on top of DuckDB's Spark API (`spark_compat.py`), left over from batch 7.
- The local `spark` session: readers, writers, catalog, createDataFrame, query progress and profiles (`spark_local.py`).
- The Spark SQL built-ins written as DuckDB SQL (`spark_builtins_sql.py`).

The run was at `main` 6e76117fe (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 7 (sample cap 150, seed 7), on DuckDB 1.5.6. Every module has more points than the cap, so 150 of each were sampled.

Tests left out of every run:
- `test_spark_read_options.py::test_the_docs_page_is_up_to_date` and `test_spark_api.py::test_the_coverage_page_matches_the_api` read pages under `docs/`. The mutator's copy of `sidecar/` doesn't have that folder, so with them in, every mutant is "killed" by a missing file. A first `spark_local.py` run that had the docs-page test in scored 150 of 150 that way, and it was thrown away. As in batch 7, `test_notebook.py`, `test_databricks_coverage.py` and `test_sql_exec.py` were not used.
- `test_spark_compat.py::test_these_frames_leave_no_view_behind` was deselected. It takes 41 s alone, and every mutant would have paid that.

The "before" runs used each module's own test files. The survivors were then run again three ways:
- With the related `test_spark_*` files that the first set left out, listed in brackets as "existing".
- With the new tests.
- With both together.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_compat.py` | test_spark_compat, test_spark_compat_edges, test_spark_local, test_spark_builtins_sql, test_spark_bad_dates, test_spark_dataframe_decimals, test_spark_integer_casts, test_spark_join_drop, test_spark_select_star, test_safe_parse_readers, test_untranslatable_sql (+ existing test_spark_show, test_spark_column_methods, test_spark_column_names, test_spark_cube_rollup, test_spark_format_string, test_spark_missing_functions, test_spark_nan_rows, test_spark_summary_schema, test_spark_function_names, test_spark_sql_names, test_spark_stat_methods, test_spark_struct_fields, test_spark_divide, test_spark_df_extras, test_pyspark_sql_names, test_spark_cast_names; + test_spark_compat_mutation) | 150 of 1,214 | 88 / 62 | **58.7%** | 141 / 9 | **94.0%** | 7, and 2 left |
| `spark_local.py` | test_spark_local, test_spark_local_edges, test_spark_local_more_edges, test_spark_merge, test_spark_read_options, test_spark_refusals, test_spark_write_types, test_append_conversions, test_spark_compat, test_spark_compat_edges, test_duckdb_try_cast (+ existing test_spark_writer, test_spark_catalog_names, test_spark_csv_options, test_spark_json_parquet_options, test_spark_json; + test_spark_local_mutation) | 150 of 1,025 | 86 / 64 | **57.3%** | 138 / 12 | **92.0%** | 4, and 8 not reached |
| `spark_builtins_sql.py` | test_spark_builtins_sql, test_spark_builtins_sql_edges, test_spark_session_functions (+ test_spark_builtins_sql_mutation) | 150 of 591 | 113 / 37 | **75.3%** | 140 / 10 | **93.3%** | 8, and 2 not reached |

How the survivors were killed:
- `spark_compat.py`: 18 of the 62 by the existing files and 35 by the new tests.
- `spark_local.py`: 29 of the 64 by the existing files and 23 by the new tests.
- `spark_builtins_sql.py`: the existing `test_spark_hash`, `test_spark_hash_refused`, `test_spark_higher_order`, `test_spark_format_string`, `test_spark_views_text`, `test_spark_sql_names` and `test_spark_timestamp_casts` killed none of the 10 left after the new tests.

All three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **spark_compat.py**:
  - `bool_or` and `some`, `bin` in 64-bit two's complement, and `split` with a limit, for a pattern and for characters.
  - `format_string` and `printf`, including a NULL as `null` and a column refused as a format.
  - Seeded `rand`, which repeats for a seed and stays in [0, 1).
  - `first` and `last` with and without `ignorenulls`.
  - `log` with and without a base.
  - The functions that read a number argument as text (`upper(14)` is `'14'`), each one.
  - The `percentile_approx` note, printed only for an accuracy it ignores.
  - `grouping()` refused under a plain `groupBy`.
  - `F.expr` names.
  - `Window.rangeBetween` called on the class, and Window's bounds as PySpark's.
  - `Column.substr` with ints, with Columns, and refused when mixed.
  - The bitwise methods and their names.
  - `between` with infinite and NaN bounds.
  - `getField`, and the names of a struct field and a map key.
  - A map, a struct and an interval in `show()`.
  - A grouped `avg` and `mean`, and dict `agg` counting a column or rows.
  - Rows for structs inside an array.
  - The function list.
  - `install()` run twice.
  - A summary percentile, `drop()` given a number, and `select("`a.b`.*")`.
  - → `test_spark_compat_mutation.py`.
- **spark_local.py**:
  - createDataFrame of single values, of an Arrow table, and refused where a typed value is a row.
  - `_nan` only for a float NaN.
  - A CSV `sep` that DuckDB's sniffer wouldn't pick.
  - `mode("ignore")` writing a new file and keeping an old one.
  - The query progress hint between 0 and 100, with no session and with a closed connection.
  - The cell connection watch.
  - DuckDB's JSON profile in its 1.x and 2.0 shapes, and a real profile.
  - A Parquet or JSON read schema matched by name.
  - A FAILFAST message naming a column that has a quote in its name.
  - DDL `decimal` defaults, and a COMMENT with an escaped quote and a comma.
  - A three-part `USE` refused.
  - A refused write option's text.
  - → `test_spark_local_mutation.py`. It sets LOCALAPPDATA to a temp folder, and the progress and connection tests swap module state only through monkeypatch.
- **spark_builtins_sql.py**:
  - `split` with a limit: Spark's empty-pattern rule (`split('abc', '', 1)` is `['a']`), and a limit from the data refused.
  - `replace` with no replacement.
  - `format_string`:
    - Two's complement by type size for `%x` and `%o`.
    - Explicit argument indexes, and too few arguments.
    - Each refusal message.
    - A format taken from the data.
  - `typeof(signum(...))`.
  - `stack` of one value and of no rows.
  - `try_to_binary` for each format and for a column.
  - `hash()` and `xxhash64()` of nothing, and `hash(-2147483648)` as Spark's int.
  - `extract` from text.
  - `to_char` with a number as its format.
  - `to_csv`, `map_zip_with` and `extract` calls that Spark refuses still stopping.
  - → `test_spark_builtins_sql_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `spark_compat.py` | 1395 | `return False` → `None` | Its callers read it only as true or false. |
| `spark_compat.py` | 1821 | `GroupedData.avg` returns nothing | `install()` replaces `avg` and `mean` with `_numeric_aggregate` before any call. |
| `spark_compat.py` | 2059 | `and` → `or` for a map in `show()` | A struct value reaches `_pretty` as a tuple or Row, never a dict. |
| `spark_compat.py` | 1092 | a function with no signature returned as None | Every F function has a signature. |
| `spark_compat.py` | 2010 | Float digits searched from 1 place, not 0 | 1 place already gives the shortest text that reads back. |
| `spark_compat.py` | 1313 | an unclosed backtick checked as `end < 1` | `find` starts after the backtick, so `end` is never 0. |
| `spark_compat.py` | 1486 | `strict=True` → `False` in `zip` | The lengths are checked just above, or one list is built to match the other. |
| `spark_local.py` | 356 | MERGE source names counted from 0 | Only the names change, and they stay unique. |
| `spark_local.py` | 1185 | the "Only UPDATE and DELETE" raise removed | An Update or Delete tree always parses to a change, so it can't be reached. |
| `spark_local.py` | 1265 | `changed` starts True | It projects the same columns unchanged. |
| `spark_local.py` | 1317 | `strict=True` → `False` in `zip` | The rows and the values made from them have the same length. |
| `spark_builtins_sql.py` | 1024 | `_uncast` negated | sqlglot writes a column and its identifier the same, and a recast timestamp is the same timestamp. |
| `spark_builtins_sql.py` | 684 | `csc` template arity 1 → 0 | sqlglot writes `csc` for DuckDB by itself, giving the same value. |
| `spark_builtins_sql.py` | 716, 717, 562 | the safe flag of `to_timestamp_ntz` and `try_to_timestamp` | With ANSI off, the only Spark mode here, sqlglot writes text that isn't a timestamp as NULL either way. |
| `spark_builtins_sql.py` | 537 | `timestamp_micros` through the decimal path | Multiplying by 1 gives the same microseconds. |
| `spark_builtins_sql.py` | 405 | `map_zip_with` passes the lambda as a third argument | The template has no `fj_arg2`, so it is never used. |
| `spark_builtins_sql.py` | 641 | `window()` with no arguments skips the NULL filter | Such a call is an error either way. |

Left without a test:
- **Not reached without an attached lakehouse**:
  - `spark_local.py` lines 375, 565, 977, 1099, 1124 and 1493 (×2): the MERGE preview, the partition columns in `listColumns`, rebinding tables after a new attach, `CREATE TABLE` properties, `INSERT INTO` a missing table, and `DeltaTable.forPath`.
  - Their tests need a OneLake fake that these runs didn't load.
- **Not reached by any test**:
  - `spark_builtins_sql.py` line 1160: the hash refusal after another rewrite writes `hash` out as text.
  - `spark_builtins_sql.py` line 621: `window()` columns without a GROUP BY.
- **Left**:
  - `spark_compat.py` line 2292: `install()`'s once-only flag. Running it twice gave the same results in the test.
  - `spark_compat.py` line 1571: the summary percentile divided by 101. No small data set picked a different value.
  - `spark_local.py` line 1242: a schema list with a name that isn't text, which PySpark refuses anyway.

### Real bugs found

- #2554: `extract(field FROM s)` and `date_part('field', s)` on a plain text column fail with a DuckDB binder error. Spark reads the text as a timestamp. A text literal and `CAST(s AS STRING)` work.
- #2555: on DuckDB 1.5.6, `F.lit(float('nan'))` and Column comparisons with a NaN float become NULL. Spark's answer is NaN, true or false. DuckDB 2.0 is right.
- #2557: `spark.read.csv` without `sep` lets DuckDB guess the delimiter, where Spark always uses a comma.

None of them is security-relevant.

### Not in this batch

- The other `spark_*` modules: `spark_sql.py`, `spark_infer.py`, `spark_struct_fields.py`, `spark_df_extras.py` and `spark_api.py`.
- The academy PySpark lab test (`test_academy_pyspark_lab.py`), which is in the academy area and was left out.

