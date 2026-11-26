# Mutation testing, batch 233

A re-score of `databricks_variant.py`, which batch 28 left at **95.3%** on point mutants and batch 36 at **78.9%** on boundary mutants, on today's `main`. This run counts both kinds together (`--kinds all`).

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each test set was run with `-x`. The machine was shared with three other mutation runs, so the per-mutant timeout was raised from 120 to 600 seconds for this batch (by setting `mutate.TIMEOUT` from a wrapper, with the tool unchanged); no mutant's run timed out. The module now has 267 mutants, so 150 were sampled. Three tests in `test_databricks_coverage.py` read `docs/` (`test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date`, `test_the_docs_link_the_list`), so they were deselected.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_variant.py` | test_databricks_variant_edges, test_databricks_coverage (3 tests deselected), test_databricks_variant_mutation, test_boundary_mutation (+ test_databricks_variant_rescore) | 150 of 267 (139 + 11) | 141 / 9 | **94.0%** | 132/139 → 133/139 | 9/11 → 9/11 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **databricks_variant.py**:
  - Line 35, the exact context's `2 * 38 + 2` digits to `2 * 38 - 2`: the widest VARIANT decimal (38 digits) cast to `DECIMAL(38, 38)` needs 76 digits while it is rounded. With 74 it raised Python's `InvalidOperation` instead of giving `try_variant_get`'s NULL or `variant_get`'s `INVALID_VARIANT_CAST`.
  - → `test_databricks_variant_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `databricks_variant.py` | 35 (2) | the exact context's digits `2 * 38 + 2` to `3 * 38 + 2` or `2 * 38 + 3` | Equivalent: rounding needs at most 38 + 38 = 76 digits, and both still have more. |
| `databricks_variant.py` | 64 | `value < 0` to `<=` | Equivalent, as in batch 36: zero returns earlier. |
| `databricks_variant.py` | 79 | 20 characters to 21 for a BIGINT | Equivalent, as in batch 28: JSON has no leading zeros, so 21 characters is always past 2^63. |
| `databricks_variant.py` | 84 | `max(-exponent, 0)` to `max(-exponent, 1)` | Equivalent, as in batch 28: the scale is only compared with 38. |
| `databricks_variant.py` | 219 | `at < len(text)` to `<=` | Equivalent, as in batch 36: at the end the step pattern matches nothing. |
| `databricks_variant.py` | 295 | `and` to `or` in the double check | Equivalent, as in batch 28: a double read back from its text always has an exponent. |
| `databricks_variant.py` | 318 | `items`'s `return []` for a scalar dropped (so it gives NULL) | Equivalent: `variant_explode` unnests the list, and unnesting NULL or an empty list gives no rows; `variant_explode_outer`'s `LEFT JOIN` gives its one row of NULLs either way. |

