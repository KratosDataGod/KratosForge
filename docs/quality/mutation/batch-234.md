# Mutation testing, batch 234

A re-score of `spark_read_options.py`, which batch 42 left at **82.4%**, on today's `main`.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each test set was run with `-x`. The machine was shared with three other mutation runs, so the per-mutant timeout was raised from 120 to 600 seconds for this batch (by setting `mutate.TIMEOUT` from a wrapper, with the tool unchanged); no mutant's run timed out. The module has 17 mutants, so all were run. `test_spark_read_options.py::test_the_docs_page_is_up_to_date` reads `docs/`, so it was deselected.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_read_options.py` | test_spark_read_options (one test deselected), test_spark_read_options_mutation (+ test_spark_read_options_rescore) | 17 of 17 (17 + 0) | 14 / 3 | **82.4%** | 14/17 → 16/17 | none | 16 / 1 | **94.1%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_read_options.py**:
  - Line 98, the `__name__ == "__main__"` guard negated or swapped (2 mutants): batch 42 left them as equivalent for the tests. Importing the module in a new Python process now must print nothing and succeed, so a module that rewrote the docs page on import would fail.
  - → `test_spark_read_options_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_read_options.py` | 46 | `has_schema=False` to `True` | Equivalent, as in batch 42: each option is checked with no value, and the schema only matters for options that take one. |

