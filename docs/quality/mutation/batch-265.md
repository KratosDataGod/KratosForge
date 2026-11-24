# Mutation testing, batch 265

One module that had no score before, run with `--kinds all`:
- Delta's `replaceWhere` overwrite on this PC: turning the condition into the predicate delta-rs replaces by, and checking every row written against it (`spark_replace_where.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each pytest session got its own fresh temporary folder, so a mutant that leaves a folder behind can't fail the runs after it. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because the machine was shared with two other mutation runs. The module has 30 mutants, so all were run; none is a boundary mutant. No test was left out, and no mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_replace_where.py` | test_academy_replace_where, test_delta_write_integrity, test_spark_refusals (+ test_spark_replace_where_mutation) | 30 of 30 (30 + 0) | 27 / 3 | **90.0%** | 27/30 → 29/30 | none | 29 / 1 | **96.7%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_replace_where.py**:
  - A column in the condition is quoted as the table spells it, so `region` becomes `"Region"` and ```my col``` becomes `"My Col"`.
  - A check that DuckDB can't run says the first line of the error it stopped on.
  - → `test_spark_replace_where_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_replace_where.py` | 71 | `int(row[0]) if row else 0` to `else 1` | Equivalent: `SELECT count(*)` always returns one row, so the `else` is never taken. |
