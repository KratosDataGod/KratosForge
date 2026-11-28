# Mutation testing, batch 515

A re-score of `localdata/engine.py`, the local SQL engine of a project: its connection, T-SQL and Spark SQL statements, the project's own Delta tables and the answers it gives. Batch 3 left it at **93.3%** (point mutants only), and batch 152 at **92.7%**, 93.3% after #3045 (kinds all). The module has grown from 1,042 mutants to 1,305 since batch 152 (the 1,024-column limit, T-SQL errors in SQL Server's words, errors while a result streams, the connection given back after RECYCLE_AFTER queries), so this run takes a new sample.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder. The module has 1,305 mutants, so a sample of 150 was run. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy; the set takes about 61 s. Batch 152 ran its T-SQL files only on the survivors; here they are in the one run. Three tests in test_tsql_loud_gaps read the docs' refused list, so they were left out with `--deselect`, as in batches 9 and 152. test_sql_exec, test_local_sql_stop and test_localdata_kills were left out, as in batches 3 and 152, because with them each surviving mutant takes about two minutes. That can only lower the "before" score.

The test set was batch 3's (test_local_data, test_local_data_engine_edges, test_local_data_engine_more_edges, test_duckdb2_shapes, test_localdata_long_paths, test_spill_leftovers, test_local_delta, test_local_delta_dml, test_local_explain, test_awkward_paths, test_local_transform, test_localdata_manager_edges, test_safe_parse_readers), test_localdata_engine_mutation, test_localdata_engine_refused_write, test_localdata_engine_rescore, and batch 152's T-SQL files (test_tsql_changes, test_tsql_routines, test_local_tsql, test_tsql_windows, test_tsql_loud_gaps, test_delta_read_spark_tables).

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. One survivor was left by the first "after" run (the Spark SQL DROP). It got a test and was run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/engine.py` | batch 3's set, test_localdata_engine_mutation, test_localdata_engine_refused_write, test_localdata_engine_rescore, and batch 152's T-SQL files (+ test_localdata_engine_mutation_b) | 150 of 1,305 (149 + 1) | 111 / 39 | **74.0%** | 111/149 → 144/149 | 0/1 → 1/1 | 145 / 5 | **96.7%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/engine.py**:
  - A T-SQL table of 1,025 columns is refused with SQL Server's words, naming column `c1024` and the table without its schema. A table made from 1,025 values is refused too. A table of 1,024 columns, and a small one, are made.
  - One failing T-SQL statement isn't named as one of a script. A script names the failing statement ("Statement 2 of 3") and says the last statement didn't run. A refused translation (a CTE name used twice) has SQL Server's number, 239.
  - An overflow while a T-SQL result streams is in SQL Server's words (Msg 8115). In a script, it names the last statement as "Statement 2 of 2". A DuckDB result keeps DuckDB's words. A stream's own OSError comes out as it is, not as "DuckDB stopped the query".
  - The log keeps the first 1,000 characters of DuckDB's words.
  - TRUNCATE TABLE on the project's own Delta table is refused, and so is DROP TABLE in Spark SQL.
  - A data file outside a Delta table's folder is refused, quoting the first 200 characters of its path.
  - A table another writer makes is read by the next statement. Only the table another writer committed to is read again. A commit that left the log folder's time alone is still seen, by its next commit file.
  - The profile check after a write's commit says it committed. A change says how long it took. A change stopped part way says it was cancelled.
  - A T-SQL MERGE into a table named without its schema refuses a row matched twice.
  - A persistent connection is given back after RECYCLE_AFTER queries. The table list gives back the cursor it borrowed while a query held the connection.
  - A file put under the name of a table the user made is an issue with the file's path.
  - A query without a limit returns 1,000 rows. Each statement says how long it took. An analyzed query names its three slowest steps.
  - A cut answer under the most rows says to pass a larger limit, and has no "more than one answer holds" sentence. The note on lines above a header says "it is" for one line and "they are" for more.
  - A statement right after a closed, nested comment keeps its first letter (`/* a /* b */ c */SELECT 1` is a SELECT).
  - → `test_localdata_engine_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/engine.py` | 235 | `min(..., default=0)` to `default=1` | Equivalent, as batch 3 said (then line 140): a Delta table always has at least one commit, so the default is never used. |
| `localdata/engine.py` | 495 | `queries_since_open` starting at 1 | Equivalent: opening the connection sets it to 0 before any query counts. |
| `localdata/engine.py` | 622 | the `CHECKPOINT` on close run for a memory project, not a persistent one | Equivalent: DuckDB folds the write-ahead log into the file itself when its connection closes (checked: no `.wal` file is left either way), and a CHECKPOINT in memory does nothing. |
| `localdata/engine.py` | 1426 | `_write(..., dry_run=False)` default to `True` | Equivalent: its one caller always passes `dry_run`. |
| `localdata/engine.py` | 1447 | a write summary's `bytes` starting at 1 | Equivalent: every write sets it from what it wrote, except CREATE TABLE IF NOT EXISTS on a table that's there, whose answer shows only the table, mode, version and rows. `write_delta` can't ask for IF NOT EXISTS. |

**Main changed after this run (2026-11-28):** #3361 (time travel survives Delta log cleanup) and #3235 (MCP server hardening) changed `localdata/engine.py` after the run. The score above is for the module at the run's `main`, as named on this page, and it was not run again on the new module. The new tests pass unchanged on today's main.
