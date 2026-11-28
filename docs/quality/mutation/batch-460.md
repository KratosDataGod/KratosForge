# Mutation testing, batch 460

One module not scored before, run with `--kinds all`:
- SQL cells on a Snowflake warehouse, read-only: each statement read with sqlglot and refused unless it is a query, SHOW, DESCRIBE or EXPLAIN, sent through Snowflake's SQL API, its result read from as few partitions as the preview needs (or all of them, within the byte and row limits, for a whole result), typed from its rowType, plus Plan, Catalog and Cancel (`engines/snowflake_warehouse.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 287 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_snowflake_sql_engine, test_snowflake_warehouse_cov31 and test_engines_service_uncapped.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. A fake client stands in for Snowflake, as in the existing tests: no network and no credentials. The "after" run reran the 29 survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_warehouse.py` | test_snowflake_sql_engine, test_snowflake_warehouse_cov31, test_engines_service_uncapped (+ test_engines_snowflake_warehouse_mutation_b) | 150 of 287 (145 + 5) | 121 / 29 | **80.7%** | 120/145 → 144/145 | 1/5 → 5/5 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_warehouse.py**:
  - A statement may run two hours, and the session follows it a minute longer: it sends a timeout of 7,200 seconds and waits 7,260.
  - 10,000 partitions are read and 10,001 are refused. Exactly the partition limit is read.
  - Catalog lists at most 1,000 entries.
  - A statement without `->>` isn't a chain. A command with nothing after it isn't one either. A command's tail after other tokens is read again for a chain.
  - A refused statement is named by its first word, without the leading spaces.
  - A negative precision reads as the smallest decimal, not as text.
  - A preview keeps only its rows of a larger first partition. A result of one row is a whole result. A row count that isn't a number is left out. An answer without partition info keeps its rows.
  - Declared partition sizes over the byte limit are refused before any partition is read; sizes at the limit are read. Rows of exactly the byte limit are kept, and one byte less of limit refuses them.
  - An error reports the time it took.
  - Plan leaves out answer rows that aren't lists or are empty. Catalog keeps the database and schema Snowflake names for each object.
  - Cancel says True for a running cell. The adapter describes this engine.
  - → `test_engines_snowflake_warehouse_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/snowflake_warehouse.py` | 171 | `int(col.get("precision") or 38)` to `or 39` | Equivalent: the precision then goes through `min(max(precision, 1), 38)`, so 38 and 39 both give 38. |
