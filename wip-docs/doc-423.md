# Mutation testing, batch 423

One module not scored before, run with `--kinds all`:
- `from delta.tables import DeltaTable` in a PySpark exercise: the exercise's Delta tables found by name or by path, updated, deleted from and merged into by delta-rs, with Delta's rule that a table row may match only one source row in a MERGE (`academy/pyspark_delta.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 87 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_delta_write_integrity, test_academy_pyspark_streaming and test_academy_pyspark_lab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests write their own Delta tables with delta-rs in a temporary folder and stand in for the spark session, so no learner script runs.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pyspark_delta.py` | test_delta_write_integrity, test_academy_pyspark_streaming, test_academy_pyspark_lab (+ test_academy_pyspark_delta_mutation_b) | 87 of 87 (87 + 0) | 51 / 36 | **58.6%** | 51/87 → 87/87 | none | 87 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pyspark_delta.py**:
  - A condition or value is SQL text or a Column's expression, and anything else is a TypeError in words.
  - A merge source must be a DataFrame. Without a name of its own it is called `source`.
  - forName finds the exercise's table, or says there is none, and falls back to the exercise's session. forPath takes only a Tables/ path, with or without the lakehouse prefix. isDeltaTable answers for a name or a path.
  - alias keeps the table, toDF reads it, and history lists each commit.
  - update and updateExpr need the columns to set and apply the condition when there is one. delete works with or without a condition.
  - optimize, vacuum and the restores say they aren't here.
  - Each MERGE clause runs with its condition and values. Without an alias, the target is the table's own name.
  - A clause that sets values needs them. A merge with no clauses, or with schema evolution, isn't run.
  - Two source rows for one table row stop the merge, whether ON is text or a Column. Only inserting, or one unconditional delete, skips that check. A condition the check can't read stops the merge, naming the error.
  - → `test_academy_pyspark_delta_mutation_b.py`.

### Left

None.
