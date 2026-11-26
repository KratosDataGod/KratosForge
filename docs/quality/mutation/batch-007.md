# Mutation testing, batch 7

The SQL translation and the notebook's own code, which no open pull request is changing:
- The Spark SQL translation to DuckDB (`sql.py`).
- The notebook files, outputs, history and `%fs` magic (`notebook.py`), leaving out the kernel and the debugger.
- The SQL engines on this PC: DuckDB, SQLite and Polars (`engines/local_sql.py`).
- The local `notebookutils` (`notebookutils_local.py`).

The run was at `main` 7c3dbd0ec (2026-11-12), with `sidecar/tools/mutate.py` and the same method as batches 1 to 6 (sample cap 150, seed 7), on DuckDB 1.5.6 and sqlglot 30.23. Every module has more points than the cap, so 150 of each were sampled. For `notebook.py`, 80 of the 150 fall outside the kernel and debugger (the `Kernel` class and the kernel magics, lines 628 to 2040, and `set_paused`); only those 80 are counted.

`test_notebook.py`, `test_databricks_coverage.py` and `test_sql_exec.py` were not in any run: they read files outside `sidecar/` (such as `examples/data/sales.csv`), which the mutator's copy of `sidecar/` doesn't have, so with them in every mutant is "killed" by a missing file.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sql.py` | test_sql_compat, test_sql_editor, test_sql_run_edges, test_bounded_previews, test_untranslatable_sql, test_tsql_contract, test_shell_and_decimals_edges, test_safe_parse_readers, test_time_travel, test_duckdb_try_cast, test_sql_lambdas and the test_spark_* SQL files (+ test_sql_mutation) | 150 of 894 | 108 / 42 | **72.0%** | 144 / 6 | **96.0%** | 6 |
| `notebook.py` (outside the kernel and debugger) | test_notebook_files, test_notebook_files_edges, test_notebook_folders, test_notebook_shelf_edges, test_ipynb_round_trip, test_output_cap, test_outputs_hardening, test_notebook_display_edges, test_notebook_history, test_notebook_places, test_notebook_resources, test_notebook_inputs, test_notebook_run (+ test_notebook_mutation) | 80 of 150 sampled from 1,680 | 49 / 31 | **61.3%** | 80 / 0 | **100%** | 0 |
| `engines/local_sql.py` | test_engines, test_local_sql_results, test_engine_compare, test_snowflake_sql_variadic, test_untranslatable_sql and the test_spark_* SQL files (+ test_local_sql_mutation) | 150 of 245 | 108 / 42 | **72.0%** | 142 / 8 | **94.7%** | 6, and 2 behind a bug |
| `notebookutils_local.py` | test_notebookutils_fs, test_notebookutils_helpers, test_notebookutils_local_edges, test_notebookutils_parity, test_dbutils, test_keys, test_paging_complete, test_contract_onelake (+ test_notebookutils_local_mutation) | 150 of 449 | 128 / 22 | **85.3%** | 149 / 1 | **99.3%** | 1 |

The "after" numbers re-run each survivor with `--only` and the new tests in. All four new test files also pass on DuckDB 2.0 and on sqlglot 30.22 and 30.27, the ends of the range `pyproject.toml` allows.

### What the survivors were, and the tests that now kill them

- **sql.py**:
  - DuckDB SQL that Spark can't read being read as DuckDB, a key and value call with an odd number of arguments never being read that way, and a lone `X '0A'` read as binary while `t.x '0A'` is left alone.
  - Counting a call's arguments: brackets, square brackets and braces, an unclosed call, a call inside another, and a `datediff` that sits inside brackets.
  - An empty `struct< >` named whichever way it is spelled, including one at the very end of the query and one sqlglot reads without an error.
  - Which name the `current_database()` and `current_schema()` refusals give.
  - Where Spark puts NULL for `array_sort` and `sort_array`, a whole number in brackets, `schema_of_json` for equal types and for a number one past BIGINT, `from_json` as a whole expression, and `str_to_map`.
  - `timestampdiff` in minutes, hours, months, quarters and years, run on DuckDB.
  - `substring` with a start of 0, 1, 2 and -3, as a number and as a column.
  - The Spark name of a rewritten scalar subquery.
  - `bind_tables` binding a table unless it is told the name is local, an older version of a table being superseded, a Databricks `@` time point, and `point_args`.
  - → `test_sql_mutation.py`.
- **notebook.py**:
  - An error at and just past the character cap, and the preview row count for a LazyFrame and for pandas.
  - Clearing outputs with and without waiting, what a run's snapshot holds, and the query progress it reads.
  - The output router's return value, and the `%fs` magic: ls, cp with and without `-r`, a `/mnt` path refused, and put.
  - Value sizes and memory, the watch cap, and durations in s, ms, µs and ns.
  - Notebook folders down to the depth limit, the store's list, cell metadata merging, a save keeping compute and workspace, what delete says about resources, and a shelf move whose copy vanished.
  - → `test_notebook_mutation.py`.
- **engines/local_sql.py**:
  - The 256 MB limit in the descriptor and in the error text, and `Done in` for a statement with no rows.
  - A second cell refused at once while one runs, and the seconds an error cell reports.
  - The row and byte limits in `_drain` and `_keep` at their edges, and the preview across several batches.
  - The DuckDB connection's external access and extension settings being off.
  - Which parser reads Snowflake and Databricks SQL, Databricks' `timediff`, a runnable Snowflake cell, and the Snowflake plan's label.
  - An INSERT's count, the DuckDB 2.0 relation path forced on 1.x, the catalog's view and table types, SQLite's and Polars' no-row results, a one-column Polars result, and the Polars adapter.
  - → `test_local_sql_mutation.py`.
- **notebookutils_local.py**:
  - Each local change returning True, a plain local path, `head`'s default length, and the default `display`.
  - `ls` of a file when OneLake answers 400, `cp` of `Files` with recurse, and `mv` keeping its source when a file didn't land, both on OneLake and locally.
  - The default workspace, `runMultiple` clearing task values only at the top level, `validateDAG`, the UDF function list, and the variable library note printed once.
  - → `test_notebookutils_local_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `sql.py` | 434 | the comparator check negated | Only reached on sqlglot 30.24 and later; on 30.23, where the run was, `array_sort` takes the older branch. The new test kills it on 30.27. |
| `sql.py` | 673, 675 | depth 0 → 1 for `from_json` | The depth only numbers the lambda names, which stay distinct. |
| `sql.py` | 699 | `and` → `or` when putting arguments into a template | The only unqualified columns in the templates are the `fj_arg` ones. |
| `sql.py` | 1047 | `copy=False` → `True` | Copies a node that was already copied. |
| `sql.py` | 1460 | `wrap` default True → False | DuckDB's `LAMBDA x : …` form always writes without brackets. |
| `engines/local_sql.py` | 93 | `_cancelled` starts True | `execute` sets it to False before anything reads it. |
| `engines/local_sql.py` | 358 | `or` → `and` choosing the Arrow reader | Both readers give the same batches. |
| `engines/local_sql.py` | 437, 439 | `strict=True` → `False` in two `zip`s | SQLite's rows and names always have the same length. |
| `engines/local_sql.py` | 481, 488 | `result_rows > 0` → `> 1` in SQLite's batch loop | `_keep` checks the same row and byte limits again on the table the loop builds, so only how early it stops changes. |
| `notebookutils_local.py` | 236 | the size of the `Files` or `Tables` root entry 0 → 1 | Nothing reads that size: moving `Files` itself is refused. |

### Real bugs found

- #2549: on DuckDB 1.x, a DuckDB engine cell with no statement in it, such as only a comment, fails with an internal `AttributeError`, where DuckDB 2.0 says it is done. The two `local_sql.py` survivors on line 357 are the branch that should answer it; on 1.x no input reaches it. They are left for the fix.

### Not in this batch

- `spark_compat.py`: 112 of its 150 sampled mutants survived its own tests, too many to handle alongside the others. It is left for its own batch.
- `spark_local.py`, left with `spark_compat.py` for a later batch.
- The notebook kernel and debugger, as asked.
- `app_settings.py`, `policy_schema.py`, `audit.py`, `service.py`, `audit_chain.py` and `retention.py`, which open pull requests still change.

