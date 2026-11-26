# Mutation testing, batch 302

One engine module that had never been scored before: `engines/snowflake_warehouse.py`. It ran with `--kinds all`, so the score counts the boundary mutants (`<` to `<=`, `>` to `>=`) as well as the earlier kinds, and the table shows each part.
- The Snowflake SQL engine: its read-only check (the first word it names, and `->>` chains inside a command's tail), its statement and partition limits, how a whole result is checked against Snowflake's reported size and row count, previews, cancel, the time a cell reports, plan and the catalog.

The run was at `main` 29e00e70d (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 287 mutants, and 150 were sampled. Test files were given to the tool as paths relative to `sidecar/`. They passed once on the unmutated copy first. Each pytest session got a new temporary folder, removed when the session ended, so a mutant can't leave a folder that breaks later runs. No `-k` filter was used. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was deselected.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, so the run can't score a false 100%. The new tests pass on DuckDB 2.0.0 and on sqlglot 30.23.0 and 30.27.0 too.

One mutant timed out in both runs: it stops `cancel` from marking the cell cancelled (line 369), so the test that cancels mid-cell waits forever. Run again on its own with a 900-second limit, it was killed by a failing assertion rather than by the limit, so it counts as killed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_warehouse.py` | test_snowflake_sql_engine, test_snowflake_warehouse_cov31, test_engines_service_uncapped (+ test_snowflake_warehouse_mut302) | 150 of 287 (145 + 5) | 121 / 29 | **80.7%** | 120/145 → 143/145 | 1/5 → 5/5 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_warehouse.py**:
  - The limits are two hours a statement, 10,000 partitions a result and 1,000 catalog rows. A statement is followed for its limit plus one minute.
  - Exactly 10,000 partitions are read, and 10,001 are refused before any is read.
  - A refused statement is named by its first word in capitals, without the space before it. Text that doesn't start with a word is "This statement".
  - A command with nothing after it isn't a `->>` chain. A `->>` inside a string in the next statement doesn't make a command's tail a chain either. The check answers `False`, not just something falsy.
  - A row count Snowflake sends as text isn't taken as its count.
  - A whole result exactly at the size limit is kept, both as Snowflake reports it and as it is read. A reported size over the limit is refused before any further partition is read.
  - A result with no partition list is read from the first answer.
  - A preview keeps only the rows asked for, even from a larger first partition. Asking for one whole row makes a whole result.
  - An error reports how long the cell took, not a clock reading.
  - `cancel` while a cell runs answers yes.
  - A precision below one is raised to one digit, so the column stays a decimal.
  - Plan skips empty rows and anything that isn't a row.
  - The catalog keeps the database and schema each row names, not the session's.
  - The adapter describes the Snowflake engine.
  - These tests are in `test_snowflake_warehouse_mut302.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/snowflake_warehouse.py` | 115 | `tokens[i + 1].token_type` to `tokens[i - 1].token_type` in `_flow_chain` | Equivalent: the text read again is still the next token's. After a command, the tokenizer gives what follows as one string token, or nothing at all, so the only token whose text could hold `->>` is that string. A `->>` token on its own is caught one line earlier. |
| `engines/snowflake_warehouse.py` | 171 | default precision `38` to `39` | Equivalent: the precision is capped at 38 on line 178, so 38 and 39 build the same decimal type. |
