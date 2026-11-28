# Mutation testing, batch 421

One module not scored before, run with `--kinds all`:
- Databricks SQL exercises: the learner's Databricks SQL translated by the DuckDB engine's own Databricks path and checked like a DuckDB lab answer, with a table the answer writes compared after one run and after two (`academy/dbsql_lab.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_exercises was left out of the test set because each of its runs takes about 875 seconds; that can only lower the "before" score. The module has 87 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_dbsql_lab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests run only the test's own SQL in the lab's locked in-memory DuckDB.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/dbsql_lab.py` | test_academy_dbsql_lab (+ test_academy_dbsql_lab_mutation_b) | 87 of 87 (85 + 2) | 71 / 16 | **81.6%** | 71/85 → 85/85 | 0/2 → 2/2 | 87 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/dbsql_lab.py**:
  - An answer of exactly 100,000 characters is read, and one more character is refused.
  - An answer the reader recurses on is refused in words.
  - SQL that can't be parsed calls no functions.
  - A written table is read one row past the row limit. Only a table over the limit is refused.
  - The comparison names the columns, or says "none". It counts the rows that differ, and says whether it ran the answer twice.
  - An answer that fails says whether it failed on its first run or its second, and gives the error's first line.
  - → `test_academy_dbsql_lab_mutation_b.py`.

### Left

None.
