# Mutation testing, batch 512

A re-score of `warehouse.py` on today's `main`, with `--kinds all`. It runs a query or a change on a Fabric warehouse: the checks that say what each statement is, the connect with its retries, the run with its parts and commit, and the result's Arrow types. It was last scored in batch 6 at **100%** (point mutants only), and its boundary mutants alone in batch 156 at **90.9%**. Since then it has gained table clones (WR-226), a snapshot's timestamp, loads and reads outside the warehouse refused by name, a run of statements with a part each (S14), and the notes on warehouse times (WR-221), so the sample is new too.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 502 mutants, so a sample of 150 was run.

The test set was the files of batches 6 and 156: test_warehouse_connect, test_warehouse_edges, test_warehouse_precondition, test_warehouse_queries, test_parse_errors_plain, test_safe_parse_readers, test_sql_worker_inprocess, test_warehouse_mutation and test_warehouse_boundary.

The new tests reach no warehouse: the connection and cursor are the stand-ins of test_warehouse_mutation.py, and a clock that moves a fixed step for each read stands in where a time is rounded. Before the "after" run, they passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse.py` | test_warehouse_connect, test_warehouse_edges, test_warehouse_precondition, test_warehouse_queries, test_parse_errors_plain, test_safe_parse_readers, test_sql_worker_inprocess, test_warehouse_mutation, test_warehouse_boundary (+ test_warehouse_mutation_b) | 150 of 502 (141 + 9) | 125 / 25 | **83.3%** | 118/141 → 141/141 | 7/9 → 7/9 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **warehouse.py**:
  - A run that only reads refuses a clone by its statement, or by its shape when it can't be read, a snapshot's `SET TIMESTAMP` as statement 1, and `COPY INTO` and `BULK INSERT` by name.
  - In a change, an unreadable second batch, or a clone in it, is statement 2. Nothing after unreadable text in a batch is checked.
  - A clone at a time past the warehouse's history is refused, and one at a day ago is made, as it was then.
  - A commit the warehouse refuses for its own reason ends as "sql", keeping nothing, with the warehouse's words.
  - A connect with time to spare waits 60 seconds to log in, and one with under a second left still waits 1.
  - `join_units` makes one batch per statement.
  - In a run of statements, a statement that changes rows says how many. One with rows, or with no rows and no count, says nothing changed, and its time is to the hundredth of a second.
  - A parameter only T-SQL sees (after `'a\'`, where a backslash doesn't escape the quote) needs a value. A read's own SQL error is the driver's, with nothing chained to it.
  - A decimal of precision 38 is a decimal, and 39 is text. A time with a seventh digit carries its note, and a plain column has none.
  - → `test_warehouse_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `warehouse.py` | 735 | `0 < int(precision)` to `<=` | Equivalent, as batch 156 said: a precision of 0 is falsy, so `precision and ...` stops before the comparison. |
| `warehouse.py` | 654 | `rowcount > 0` to `>=` for the affected count | Equivalent, as batch 156 said: a count of 0 adds 0. |
