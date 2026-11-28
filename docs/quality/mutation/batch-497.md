# Mutation testing, batch 497

One module not scored before, run with `--kinds all`:
- Snowflake's and T-SQL's regular expression parameters as RE2 reads them: m and s move into the pattern as `(?m)` and `(?s)`, and c and i stay as options, with the last of c and i winning for REGEXP_COUNT and REGEXP_INSTR (`regex_flags.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 19 mutants, so all were run.

The test set was test_snowflake_regex_refusals, test_snowflake_rules_differential and test_tsql_rules_differential.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivors with `--only`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `regex_flags.py` | test_snowflake_regex_refusals, test_snowflake_rules_differential, test_tsql_rules_differential (+ test_regex_flags_mutation_b) | 19 of 19 (19 + 0) | 17 / 2 | **89.5%** | 17/19 → 19/19 | 0/0 → 0/0 | 19 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **regex_flags.py**:
  - A pattern written as text gets the flags in front as one text, such as `'(?ms)^b$'`.
  - A pattern that isn't text, such as a column or a number, is joined to the flags with `||`.
  - Both mutants gave the same query results, so the SQL tests couldn't tell them apart. The new tests check the expression the function builds.
  - → `test_regex_flags_mutation_b.py`.

### Left

None.
