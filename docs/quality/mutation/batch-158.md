# Mutation testing, batch 158

A boundary re-score of four modules first scored in batches 7, 8, 9 and 11, before `mutate.py` made boundary mutants (batch 36), run again with `--kinds boundary` only:
- Spark SQL built-ins translated for DuckDB: elt, format_string's pieces, window's NULL times and shiftleft's int and long paths (`spark_builtins_sql.py`, batch 8).
- The local SQL engines: the byte and row limits on a whole result, in DuckDB's reader and in SQLite's batches (`engines/local_sql.py`, batch 7).
- Snowflake SQL translated for DuckDB (`snowflake_sql.py`, batch 11).
- Notebook files: search's limits, a file's size limit and Recently deleted at exactly its 30 days (`notebook_files.py`, batch 9).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`), with the test files of those batches, given as paths relative to `sidecar/`. Each module has fewer than 150 boundary mutants, so all of them ran. For `snowflake_sql.py`, five tests that read `docs/` were left out with `--deselect`, because `mutate.py` copies only `sidecar/`: test_snowflake_coverage's `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`, test_snowflake_sql's `test_the_docs_table_is_the_codes`, and test_snowflake_sql_parity's `test_the_docs_carry_the_table_and_say_no_account_was_used`. The tests first passed once on the unmutated copy.

The new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, on DuckDB 1.5.6 and 2.0.0. They set the size limits to the exact size of a small result or file, and fake the clock for Recently deleted. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_builtins_sql.py` | test_spark_builtins_sql, test_spark_builtins_sql_edges, test_spark_session_functions, test_spark_builtins_sql_mutation (+ test_spark_builtins_sql_boundary) | 16 of 16 | 10 / 6 | **62.5%** | 12 / 4 | **75.0%** | 4 equivalent |
| `engines/local_sql.py` | test_engines, test_local_sql_results, test_engine_compare, test_snowflake_sql_variadic, test_untranslatable_sql, test_local_sql_mutation (+ test_local_sql_boundary) | 16 of 16 | 7 / 9 | **43.8%** | 12 / 4 | **75.0%** | 4 equivalent |
| `snowflake_sql.py` | test_local_sql_mutation, test_snowflake_coverage, test_snowflake_sql, test_snowflake_sql_edges, test_snowflake_sql_parity, test_snowflake_sql_variadic, test_sql_lambdas, test_snowflake_sql_mutation | 15 of 15 | 15 / 0 | **100%** | no change needed | **100%** | none |
| `notebook_files.py` | test_notebook_files, test_notebook_files_edges, test_notebook_files_service_edges, test_cloud_service_more_edges, test_deep_nesting, test_notebook_folders, test_notebook_mutation, test_notebook_files_mutation (+ test_notebook_files_boundary) | 15 of 15 | 11 / 4 | **73.3%** | 14 / 1 | **93.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_builtins_sql.py**:
  - elt with one choice gives it.
  - shiftleft of 2147483648, one past an int, takes the long path, and 2147483647 wraps as an int does. -2147483648, which Spark reads as an int, gives 0.
  - → `test_spark_builtins_sql_boundary.py`.
- **engines/local_sql.py**:
  - A DuckDB result of exactly RESULT_BYTES is kept as the whole result.
  - Without a row limit, neither DuckDB's reader nor SQLite's batches check RESULT_BYTES. With one, rows of exactly RESULT_BYTES are kept, and one byte under is refused.
  - → `test_local_sql_boundary.py`.
- **notebook_files.py**:
  - A file of exactly MAX_FILE_BYTES is opened.
  - A notebook deleted exactly KEEP_DAYS ago isn't purged by the next delete, and it is listed in Recently deleted until its kept_until, not a microsecond after.
  - → `test_notebook_files_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_builtins_sql.py` | 454 | `m.start() > at` to `>=` in format_string | Equivalent: it only adds an empty piece of text. |
| `spark_builtins_sql.py` | 516 | `at < len(fmt)` to `<=` in format_string | Equivalent: it only adds an empty piece of text. |
| `spark_builtins_sql.py` | 799 | `len(w.expressions) >= 1` to `>` for window | Equivalent: window always has a duration after its time, so it has at least two arguments. Spark refuses one with only a time. |
| `spark_builtins_sql.py` | 983 | `-2 ** 31 <= literal` to `<` in shiftleft | Equivalent: DuckDB holds -2147483648 as an INTEGER, so the long path gives the same answer as the int path. |
| `engines/local_sql.py` | 186 | `result_rows > 0` to `>=` in `_drain` | Equivalent: `max(limit, 0)` is `limit` either way. |
| `engines/local_sql.py` | 193 | `have < keep` to `<=` in `_drain` | Equivalent: once `have` is `keep`, the slice is empty and adds no rows or bytes. |
| `engines/local_sql.py` | 403 | `duckdb_major() >= 2` to `>` | Equivalent: on DuckDB 1.5.6 neither takes the 2.0 path, and on 2.0.0 the older path gives the same rows. |
| `engines/local_sql.py` | 558 | `have < keep` to `<=` in SQLite's `_run` | Equivalent: once `have` is `keep`, the slice is empty. |
| `notebook_files.py` | 50 | `len(text) <= LINE_CHARS` to `<` in `_snippet` | Equivalent: a line of exactly LINE_CHARS is cut from 0 to LINE_CHARS, with no "…" either side, which is the same line. |

