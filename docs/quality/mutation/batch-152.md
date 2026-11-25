# Mutation testing, batch 152

A re-score of `localdata/engine.py`, which batch 3 left at **93.3%**, on today's `main`. Batch 3 ran before boundary mutants, and the module has grown from 859 mutants to 1,042 since (UPDATE and MERGE checks, renames, DuckDB 2.0's profiles), so this run uses `--kinds all` and a new sample.

The run was at `main` 4ddd3662 (2026-11-18), with the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The test set is batch 3's, plus test_localdata_engine_refused_write, and it takes about 36 s. The first run scored 117 / 33. Each survivor was then run again with the T-SQL files that also drive the engine: test_tsql_changes, test_tsql_routines, test_local_tsql, test_tsql_windows, test_tsql_loud_gaps and test_delta_read_spark_tables. They killed 12, and the "before" figures include them. Three tests in test_tsql_loud_gaps read the docs' refused list, so they were left out with `--deselect`, as in batch 9. test_sql_exec, test_local_sql_stop and test_localdata_kills were left out, as in batch 3, because with them each surviving mutant takes about two minutes. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/engine.py` | batch 3's set, test_localdata_engine_mutation, test_localdata_engine_refused_write; survivors also with the T-SQL files above (+ test_localdata_engine_rescore) | 150 of 1,042 (148 + 2) | 129 / 21 | **86.0%** | 128/148 → 138/148 | 1/2 → 1/2 | 139 / 11 | **92.7%** (140 / 150, 93.3%, after #3045) | 10 equivalent, 1 behind a known gap (killed since #3045) |

One gap was found, and #3045 has since fixed it, so the test no longer carries a strict xfail: `tables()` is meant to list the project with a cursor while a query holds the connection, "without waiting", but it calls `open()` first, and `open()` waits for the lock the running query holds. On this PC, the object list waited about 4.5 s for a 6-second query.

### What the survivors were, and the tests that now kill them

- **localdata/engine.py**:
  - An analyzed query's profile with no totals reads as zeros, and its bytes read are kept.
  - Stop with nothing running stops nothing.
  - Changed files are looked for at most every two seconds.
  - A write says how long it took, and a new table's commits are synced from version 0.
  - An append to a partitioned table keeps its partitions without naming them again.
  - An append that doesn't fit suggests schema_mode merge. An overwrite that doesn't fit doesn't.
  - A MERGE source may have a column name a table couldn't, such as `new amount`.
  - → `test_localdata_engine_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/engine.py` | 616 | `acquire(blocking=False)` to `True` in `tables()` | Behind the gap above when this batch ran. Since #3045 fixed it (the line is now 630), `test_the_table_list_doesnt_wait_for_a_running_query` kills it: checked by hand on main 70345f60, so the score after the fix is 140 / 150 (93.3%). |
| `localdata/engine.py` | 1227 | `result is None or not result.description` to `and` | Equivalent, as in batch 3 (then line 987): the query was checked to read, and a query that reads always has a description. |
| `localdata/engine.py` | 935 | `len(texts) > 1` to `> 0` | Equivalent, as in batch 3 (then line 748): one statement was already checked by DuckDB when the script was split. |
| `localdata/engine.py` | 819 (×2) | the lazy branch's `i == len(statements) - 1` and `kind == "select"` negated | Equivalent in rows: as in batch 3 (then line 539), a lazy relation and an executed result give the same rows, and `con.sql` runs a statement that isn't a query at once. Only memory use differs, which a unit test doesn't measure. |
| `localdata/engine.py` | 760 | `timeout > 0` to `>= 0` | Equivalent: `timeout and` before it already passes over a timeout of 0. |
| `localdata/engine.py` | 1233 | `by_position` starting `True` | Equivalent: with columns matched by name, the target schema is the table's fields in the query's order, so conforming by position puts each value in the same column. |
| `localdata/engine.py` | 1357 | a change's `rows_inserted` starting at 1 | Equivalent: `delta_dml.apply` always sets all three counts, even when nothing matched. |
| `localdata/engine.py` | 458 | `tmp`'s `mkdir(parents=True)` to `False` | Equivalent: the line before makes the project folder, the only parent `tmp` needs. |
| `localdata/engine.py` | 1436 | a dry run's copy linking the log and copying the data, instead of the other way round | Equivalent: delta-rs only adds files, so neither the log nor the data files of the table change either way. |
| `localdata/engine.py` | 1445 | `rmtree(copy, ignore_errors=True)` to `False` | Equivalent: the copy is this run's own folder under `tmp`, and removing it doesn't fail. |

