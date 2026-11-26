# Mutation testing, batch 18

Three modules that no open pull request is changing:
- Databricks SQL's own built-in functions on this PC, which are written as DuckDB SQL or refused by name (`databricks_builtins_sql.py`).
- The Snowflake function checks: each case's reference link, how `run` answers, and the table in the docs (`snowflake_sql_parity.py`).
- The new notebook wizard: its choices, its checks on each answer, and the cells each template writes (`wizard.py`).

`estate_service.py` was planned but left out, because it connects the tests that gate a promote. `integrations/capabilities.py` was left for later.

The run was at `main` afd8d51ba (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 17 (sample cap 150, seed 7), on DuckDB 1.5.6. The branch was then moved onto `main` dbc70faff after batch 17 merged, and its tests were run again there.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. These tests were left out because they read files outside `sidecar/`:
- `databricks_builtins_sql.py`: `test_databricks_coverage.py::test_what_the_docs_say_runs_still_runs`, `::test_the_docs_page_is_up_to_date` and `::test_the_docs_link_the_list`, `test_databricks_sql_parity.py::test_the_docs_carry_the_table`, and, in the wider set below, `test_parity.py::test_docs_page_matches_the_code`, its two `test_a_sql_rows_local_claim_follows_its_function_list` cases and `test_snowflake_sql.py::test_the_docs_table_is_the_codes`.
- `snowflake_sql_parity.py`: `test_snowflake_sql_parity.py::test_the_docs_carry_the_table_and_say_no_account_was_used`, and `test_snowflake_coverage.py::test_what_the_docs_say_runs_still_runs`, `::test_the_docs_page_is_up_to_date` and `::test_the_docs_link_the_list`.
- `wizard.py`: none.

The test files for `databricks_builtins_sql.py` were found by making `rewrite` raise and seeing which files failed. That wider set takes about 80 seconds per mutant, so the sampled run used the two files that test the module most directly (about 28 seconds). The wider set was then run on the survivors.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_builtins_sql.py` | test_databricks_coverage, test_databricks_sql_parity (+ test_academy_dbsql_lab, test_engines, test_local_sql_errors, test_local_sql_mutation, test_parity, test_snowflake_sql, test_spark_builtins_sql, test_spark_hash_refused, test_spark_higher_order, test_spark_integer_casts, test_spark_json, test_spark_lambdas, test_spark_schema_of_json, test_spark_session_functions, test_spark_text_casts, test_untranslatable_sql, test_databricks_builtins_sql_mutation) | 150 of 431 | 130 / 20 | **86.7%** | 146 / 4 | **97.3%** | 4 equivalent |
| `snowflake_sql_parity.py` | test_snowflake_sql_parity, test_snowflake_coverage_edges, test_snowflake_coverage (+ test_snowflake_sql_parity_mutation) | 150 of 243 | 127 / 23 | **84.7%** | 141 / 9 | **94.0%** | 8 equivalent, 1 that needs #2646 |
| `wizard.py` | test_wizard, test_append_callers, test_prove_service_edges (+ test_wizard_mutation) | 150 of 206 | 135 / 15 | **90.0%** | 150 / 0 | **100%** | none |

There were no timeouts.

The wider Databricks set (the files in brackets, other than test_databricks_builtins_sql_mutation) was tried on all 20 survivors first. It killed none. The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **databricks_builtins_sql.py**:
  - Each argument count and its message: `to_time`, `unix_nanos`, `bitmap_and_agg`, `ip_cidr_contains`, `hmac`, `vector_norm`, `time_to_seconds` and `is_valid_utf8` with too few and too many arguments, and "argument" against "arguments".
  - `time_diff` in microseconds counting each microsecond.
  - `to_time` reading a column with a constant format, and refusing a format from a column.
  - `try_make_timestamp_ltz` from a date and a time, read in the zone given or in the session's zone.
  - `bit_reverse` of a literal past INT staying BIGINT.
  - `variant_get` refusing a path that is a number, not a string.
  - A function of a person's own over a window staying as written.
  - A call inside another call being written too, and a statement that is itself the call.
  - → `test_databricks_builtins_sql_mutation.py`.
- **snowflake_sql_parity.py**:
  - A case linking the reference page named by its first word unless it gives one.
  - Each way `run` answers: a statement that runs only in Snowflake with its reason, the documented error counted as the same, another error by its type and first line, no rows as `None`, and only the first column compared.
  - The docs table: its heading sentence, each case that differs, the words for each outcome, and the closing list of the rest.
  - The estimates that differ giving the value their notes name (DATASKETCHES_HLL and APPROX_PERCENTILE).
  - → `test_snowflake_sql_parity_mutation.py`.
- **wizard.py**:
  - The fiscal month choices numbered from 1 with their names.
  - A malformed allowed-values or range line showing its own example.
  - Clean with no columns keeping every column, and with columns selecting them.
  - Load file needing key columns, and load folder refusing another file type.
  - SCD type 2 naming the tracked columns, or any other column when none are tracked.
  - A date dimension covering at most 200 years of 366 days.
  - A fiscal year from January being the calendar year, and one from July named by the year it ends in.
  - → `test_wizard_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `databricks_builtins_sql.py` | 69 | `strict=True` → `False` in `_whole`'s `zip` | Equivalent: `names` is cut to the number of vectors, so both sides are always the same length. |
| `databricks_builtins_sql.py` | 277 | `not isinstance(window, exp.Window) or window.this is not node` → `and` | Equivalent: `counter_diff` in another function's window is still refused with the same message, by the later pass that writes each call. |
| `databricks_builtins_sql.py` | 538 | `isinstance(node.parent, exp.Table)` negated for `variant_explode` in FROM | Equivalent: replacing the call inside its table writes the same SQL, alias included, as replacing the table. |
| `databricks_builtins_sql.py` | 547 | `bfs=False` → `True` when finding the calls to write | Equivalent: in reversed breadth-first order, an inner call still comes before the call around it. |
| `snowflake_sql_parity.py` | 162 | `MD5_NUMBER_UPPER64`'s expected value + 1 | Can't be killed until #2646 is fixed: `same()` compares numbers as floats, so two integers past 2^53 that differ by 1 compare equal. |
| `snowflake_sql_parity.py` | 186 | `_values(1, 0)` → `_values(0, 0)` for `1 / x` | Equivalent: a zero divisor is still there, so the documented error is the same. |
| `snowflake_sql_parity.py` | 391 | COUNT_IF's sample `2` → `3`, and `3` → `4` | Equivalent: two values are still above 1. |
| `snowflake_sql_parity.py` | 392 | MEDIAN's sample `4` → `5` | Equivalent: the median of 1, 2, 3 and 5 is still 2.5. |
| `snowflake_sql_parity.py` | 404 | APPROX_COUNT_DISTINCT's sample `2` → `3` | Equivalent: there are still two distinct values. |
| `snowflake_sql_parity.py` | 405 | HLL's sample `2` → `3` | Equivalent: as above. |
| `snowflake_sql_parity.py` | 411 | APPROX_PERCENTILE's `range(11)` → `range(12)` | Equivalent: DuckDB's estimate at 0.1 is still 1.0, so the case still differs as its note says. |
| `snowflake_sql_parity.py` | 437 | RATIO_TO_REPORT's sample `300` → `301` | Equivalent: the case checks the NULL row, which is still NULL. |

### Real bug found

- #2646: `same()` in `engines/databricks_sql_parity.py`, which the Snowflake and Databricks checks share, compares two whole numbers as floats. Two integers past 2^53 that differ can compare equal, so a wrong 64-bit answer such as `MD5_NUMBER_UPPER64`'s would still be recorded as "same".

### Not in this batch

`estate_service.py`, which connects the tests that gate a promote, and `integrations/capabilities.py`.

