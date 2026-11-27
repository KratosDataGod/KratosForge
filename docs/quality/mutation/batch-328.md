# Mutation testing, batch 328

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Local T-SQL MERGE as a Fabric Warehouse reads it, or refused by name (`localdata/tsql_merge.py`, WR-244).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 128 of 128 mutants ran (124 point + 4 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

No test file names the module; it is reached through `localdata/tsql.py`. The set is test_tsql_merge_edges (WR-244's own file) and test_tsql_changes (WR-213's MERGE tests), about 15 s a session. The other files that run a MERGE through the local engine were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql_merge.py` | test_tsql_merge_edges, test_tsql_changes (+ test_tsql_merge_mutation) | 128 of 128 (124 + 4) | 120 / 8 | **93.8%** | 116/124 → 122/124 | 4/4 → 4/4 | 126 / 2 | **98.4%** | 1 equivalent, 1 behind a bug |

## Bugs found

- **A MERGE's INSERT without VALUES isn't refused by name.** `_insert` refuses one only when sqlglot's `expression` is None, but sqlglot gives `False` for `INSERT` and `INSERT (id, v)` with nothing after them, so the check never holds. On a database table on this PC, `WHEN NOT MATCHED THEN INSERT;` ran and inserted the source row, and `WHEN NOT MATCHED THEN INSERT (id, v);` stopped with DuckDB's "Parser Error: syntax error at end of input". T-SQL's MERGE takes `INSERT [(columns)] VALUES (...)` or `DEFAULT VALUES` only. Pinned with `xfail(strict=True)` in `test_an_insert_without_values_is_refused_by_name` (both forms); not fixed here.

### What the survivors were, and the tests that now kill them

- **localdata/tsql_merge.py**:
  - A source table without an alias is read by its table name, so WHEN NOT MATCHED BY SOURCE reading `src.v` is refused.
  - INSERT VALUES without a column list is counted against the target's own table, not another table in the same schema, and a target written without a schema is read in dbo.
  - A MERGE after another statement and its `;` still needs its own semicolon.
  - → `test_tsql_merge_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/tsql_merge.py` | 118 | the message names `_action(whens[1])` instead of `whens[0]` | Equivalent: it is raised only when the two actions are the same. |
| `localdata/tsql_merge.py` | 161 | the "needs VALUES" raise → `pass` | Not reached because of the bug above: sqlglot never gives None there. The xfail test kills it once the check is fixed. |
