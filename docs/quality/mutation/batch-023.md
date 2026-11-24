# Mutation testing, batch 23

Three modules, the first three on this batch's list:
- Clean up: the titles and texts of each tidy-up, which lines of an f-string are left alone, leading blank lines, frozen cells, and an empty cell whose change wasn't picked (`cleanup.py`).
- SQL best practices: TOP n PERCENT before a star, UPDATE ... SET with and without FROM, LIKE inside IIF and inside grouping brackets, NOT IN over an expression, aliases and calls in correlated subqueries, table-valued functions, CTE names, a TRUNCATE then an INSERT without INTO, and statements that end early (`sqlcompat/practices.py`).
- Executions a connected tool starts: the output, row, log and record limits, how the list is read and sorted, how a stop and a cancel wait, a query error that isn't a stop, numbering cells, a failed cell, the time a call waits, and daemon threads (`integrations/executions.py`).

`integrations/registry.py` was left for a later batch. Batch 24's modules (skins, agent configuration, side by side and App Studio providers) were left to batch 24.

The run was at `main` 7ce636067 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 22 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` and `test_practice_docs.py` read `docs/`, so they were left out of the `sqlcompat/practices.py` run.

`cleanup.py` only changes cell text in memory: it writes no file and deletes nothing. Its new tests, like the others, point `LOCALAPPDATA` and `HOME` at the test's own temporary folder.

The `cleanup.py` survivors that were left were also run against `test_service_edges.py` and `test_batches.py`, which call clean up through the service; neither killed any.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cleanup.py` | test_cleanup, test_cleanup_edges (+ test_cleanup_mutation) | 150 of 317 | 133 / 17 | **88.7%** | 145 / 5 | **96.7%** | 5 equivalent |
| `sqlcompat/practices.py` | test_sql_practices, test_learn_fixes, test_learn_guides (+ test_sqlcompat_practices_mutation) | 150 of 547 | 109 / 41 | **72.7%** | 138 / 12 | **92.0%** | 12 equivalent |
| `integrations/executions.py` | test_integrations_executions, test_integrations_executions_edges, test_attach_race (+ test_integrations_executions_mutation) | 150 of 157 | 116 / 34 | **77.3%** | 147 / 3 | **98.0%** | 3 equivalent |

Two `executions.py` mutants timed out, and were counted as killed: one keeps a call waiting after its work has finished, and the other makes a stop wait on a kernel that isn't running. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **cleanup.py**:
  - "1 unused import" and "2 unused imports" in the titles.
  - Three cells listed as "Cells 1, 2 and 3".
  - The number of print lines counted from each cell's code.
  - "Cell 2 has no code." and "Cells 2 and 3 have no code."
  - An empty cell being kept when its change isn't one of those picked.
  - Leading blank lines being removed.
  - A frozen cell getting no spacing change.
  - The lines of an f-string over several lines being left alone up to, but not including, the line that closes it.
  - → `test_cleanup_mutation.py`.
- **sqlcompat/practices.py**:
  - The rules being frozen.
  - A star after `TOP 10` and after `TOP 10 PERCENT` in a view.
  - `UPDATE orders SET ...`, with or without WHERE, naming a table without its schema, and `UPDATE o SET ... FROM dbo.orders AS o` not.
  - `LIKE '%x'` inside brackets in WHERE counting, inside `IIF(...)` not, and in a WHERE clause on its own.
  - `NOT IN (SELECT b + 1 ...)` being flagged, since an expression can't be shown to drop NULLs.
  - A column called `row_number` not being `ROW_NUMBER()`.
  - A star in a subquery not feeding an INSERT, and a JOIN without ON inside brackets.
  - `CROSS JOIN` at the start being flagged once.
  - A table-valued function and a CTE name not being tables without a schema.
  - Subqueries in the SELECT list: aliases with and without AS, `dbo.u` defining `u`, a schema-qualified table, `dbo.fn(...)`, and an outer alias anywhere in the subquery.
  - `TRUNCATE TABLE dbo.t` then `INSERT dbo.t` (without INTO) being a reload.
  - Statements that end early (`TRUNCATE`, `... INSERT`, `... AS`, `... NOT IN (`, `... ORDER`, `WHERE YEAR(d)`, `VARCHAR(MAX`) being read without an error.
  - → `test_sqlcompat_practices_mutation.py`.
- **integrations/executions.py**:
  - A displayed table keeping 100 rows, the cut naming 100,000 characters, 2,000 log lines, and 200 finished records kept.
  - The list holding 50 records by default, reading the newest 200 files, sorting by start time with no start time as 0, and leaving out a running execution's result.
  - A record another save already removed being no error.
  - A view's seconds being counted from the start, to three places.
  - A query timeout being retryable and a cancel not.
  - Cells numbered from 1, the log saying so, and a failed cell not being retryable.
  - An error output keeping its traceback, and an image being named by its type.
  - A stop interrupting the kernel, waiting while it reports running, and only then ending it.
  - A cancel waiting for the work to finish.
  - A query error that isn't a stop reaching the caller, with the query slot given back.
  - A call with a time limit answering a second before it.
  - The execution, its timer and a query's stop running on daemon threads.
  - → `test_integrations_executions_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `cleanup.py` | 256 | blank-line count starts at 1, not 0 | Equivalent: only leading blank lines are affected, and those are removed anyway. |
| `cleanup.py` | 267 | `1 not in keep` → `0 not in keep` | Equivalent: line 1 is only kept when a string starts on it, so it isn't blank. |
| `cleanup.py` | 295 | `lambda i: True` → `False` when nothing is chosen | Equivalent: `plan` only reads the changes found, not the tidied cells. |
| `cleanup.py` | 337 | `ch["id"] == "empty_cells"` → `!=` | Equivalent: with no empty cells, the list of cells to remove is empty either way. |
| `cleanup.py` | 428 | `return False` → `return None` | Equivalent: the result is only used as a condition. |
| `sqlcompat/practices.py` | 248, 253, 333, 369 | `return False` → `return None` | Equivalent: each result is only used as a condition. |
| `sqlcompat/practices.py` | 351 | `return ""` → `return None` | Equivalent: the result is only compared with `"WHERE"`. |
| `sqlcompat/practices.py` | 376 | subquery range starts at the bracket, not after it | Equivalent: the bracket is neither a name nor FROM or JOIN. |
| `sqlcompat/practices.py` | 382 (2) | table-name range starts one or two places earlier | Equivalent: those places are FROM or JOIN and the token before it, which can't start a `name.name` reference. |
| `sqlcompat/practices.py` | 387 | `and` → `or` in the alias check | Equivalent in any T-SQL: the extra "aliases" are words such as WHERE or JOIN, which can't start a reference. |
| `sqlcompat/practices.py` | 391 (2) | `j == paren + 1` → `paren - 1` or `paren` | Equivalent: the token after the bracket is SELECT, and the token before any other is checked anyway. |
| `sqlcompat/practices.py` | 392 | `j + 3 >= close` → `j - 3 >= close` | Equivalent: `j + 3` is at most the closing bracket, which is neither a dot nor an opening bracket. |
| `integrations/executions.py` | 85 | after a cut, the budget left is 1, not 0 | Equivalent: no output is 1 character long, so every later one is cut either way. |
| `integrations/executions.py` | 277 | a cancel waits 3 × the grace period, not 2 | Only changes how long a cancel waits for work that hasn't stopped; a test of it would be slow and timing-dependent. |
| `integrations/executions.py` | 295 | `have_slot = False` → `True` | Equivalent: the first try for the slot sets it before anything can fail. |

### Real bugs found

None.

### Not in this batch

`integrations/registry.py`, and batch 24's modules.

