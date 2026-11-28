# Mutation testing, batch 521

One module that had never been scored before: `sql_compare.py`. It ran with `--kinds all`, so the score counts the boundary mutants (`<` to `<=`, `>` to `>=`) as well as the earlier kinds, and the table shows each part.
- Compare data on the SQL page (editor parity S34, Preview), which shows A beside B. It covers:
  - the keys and tolerances it accepts, with their limits and the wording of each refusal;
  - reading the two finished runs;
  - the counts, the page of differing rows and the seconds it took;
  - what's reported when the tables are too large to list rows;
  - the CSV export of every differing row, which never writes over a file, protects formula-like cells and turns a path that's too long into a plain error.

The run was at `main` be5ef815c (2026-11-28), with `sidecar/tools/mutate.py` and the method of earlier batches (`--cap 150 --seed 7 --kinds all`). The module has 126 mutants, so all of them ran.

How the tests ran:
- Test files were given to the tool as paths relative to `sidecar/`, and ran with `-n 2`.
- `test_sql_compare.py` starts real SQL worker processes, so each mutant takes about 20 seconds.
- The tests passed once on the unmutated copy first.
- Each pytest session got a new temporary folder, removed when the session ended, and no folder 40 or more levels deep was left.
- No `-k` filter was used. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was deselected.
- No mutant's run timed out.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, so the run can't score a false 100%. The new tests stand in for the two finished runs with tables held in memory, because the module only asks `runs.table(id)` for each side. So they start no worker. They pass on DuckDB 2.0.0 and on sqlglot 30.23.0 and 30.27.0 too.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_compare.py` | test_sql_compare (+ test_sql_compare_mut521) | 126 of 126 (123 + 3) | 96 / 30 | **76.2%** | 95/123 → 116/123 | 1/3 → 3/3 | 119 / 7 | **94.4%** | 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **sql_compare.py**:
  - **Keys:**
    - Up to 8 key columns are allowed, and a ninth is refused.
    - Keys are a list of named columns. Text, a tuple, a blank name or a name that isn't text is refused.
  - **Tolerances:**
    - Up to 50 tolerances are allowed. A 51st is refused with the tolerance message, not a later one.
    - A tolerance that isn't a mapping is refused with an example.
    - A tolerance names its column, and its value is a number. True, False, None, a list or a mapping is refused.
    - A tolerance given as text is passed on, with the column name trimmed.
  - **What a compare reports:**
    - A page lists 50 differing rows.
    - The keys are reported, and so are the seconds it took, to two decimals.
    - When rows are compared whole, the counts are only A and only B, the changed count is "none", and the listed count adds no rows that weren't counted. Each one-off row has "times" 1.
    - When the tables are too large to list rows (only checksums compared), there are no counts, no examples and no listed count, and the sides are still reported.
  - **Export:**
    - An export makes its folder with its parents and counts every row it writes.
    - An export that protects a cell says so, without offering raw values.
    - An export of tables too large to list rows is refused, and no file is left.
    - An export whose path is too long raises the plain error, and no file is left.
  - These tests are in `test_sql_compare_mut521.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sql_compare.py` | 70 | `or` swapped for `and` in `_offset`'s check | Equivalent: `verify.compare` checks the offset again with the same words, inside the same `try`, so a bad offset is refused the same way. |
| `sql_compare.py` | 71 | `raise` removed in `_offset` | Equivalent, for the same reason. |
| `sql_compare.py` | 127 | `out.get("offset", 0)` to `1` | Equivalent: `verify.compare` always returns `offset`, so the default is never used. |
| `sql_compare.py` | 149 | `e.get("times", 1)` to `0` | Equivalent: verify gives every whole-row example its `times`. |
| `sql_compare.py` | 166 | the export's starting protected count `0` to `1` | Equivalent: the count is set from the header before any row is written. |
| `sql_compare.py` | 185 | the export's offset `0` to `1` | Equivalent: the export is handed every differing row, whatever page offset is asked for. |
| `sql_compare.py` | 208 | `zip(..., strict=True)` to `strict=False` | Equivalent: the names are built from the same schema, so the two always have the same length. |
