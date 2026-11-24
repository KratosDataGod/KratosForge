# Mutation testing, batch 154

A re-score of `preview.py`, which batch 54 left at **94.7%**, on today's `main`. A table's row count now takes off the rows its deletion vectors remove, which batch 54 didn't have.

The run was at `main` 4ddd3662 (2026-11-18), with the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 76 mutants, so all of them ran. No test was left out, and no mutant's run timed out.

Before the "after" run, the new test passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. It also passes on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `preview.py` | test_preview, test_preview_mutation (+ test_preview_rescore) | 76 of 76 (74 + 2) | 71 / 5 | **93.4%** | 69/74 → 70/74 | 2/2 → 2/2 | 72 / 4 | **94.7%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **preview.py**:
  - A table with deletion vectors counts 7 rows, not its files' 10. This uses the Spark-written fixture in `tests/fixtures/delta_spark/dv`.
  - → `test_preview_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `preview.py` | 46, 64, 65, 76 | `zip(strict=True)` to `False`, `flatten=True` to `False`, the pyarrow conversion's check negated, and the file name split with `rsplit("/", 0)` | Equivalent, for batch 54's reasons (then lines 45, 64, 65 and 74). |

