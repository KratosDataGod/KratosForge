# Mutation testing, batch 267

One module that had no score before, run with `--kinds all`:
- Local T-SQL's errors in SQL Server's words, with SQL Server's error number and severity (`localdata/tsql_errors.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each pytest session got its own fresh temporary folder, so a mutant that leaves a folder behind can't fail the runs after it. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because the machine was shared with two other mutation runs. The module has 40 mutants, so all were run; none is a boundary mutant. No test was left out, and no mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql_errors.py` | test_tsql_error_numbers, test_tsql_errors (+ test_localdata_tsql_errors_mutation) | 40 of 40 (40 + 0) | 38 / 2 | **95.0%** | 38/40 → 40/40 | none | 40 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/tsql_errors.py**:
  - A divide-by-zero error is read from the first line of a message that has more lines after it. Since #3248 the module reads the whole message instead, so the test now checks that a trailing newline from DuckDB still gives SQL Server's words.
  - A schema written two ways in one statement, such as `dbo.t` and `[dbo].t`, still counts as one schema, so the missing table is named with it.
  - → `test_localdata_tsql_errors_mutation.py`.

### Left

None.
