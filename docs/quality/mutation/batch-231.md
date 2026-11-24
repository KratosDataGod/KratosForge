# Mutation testing, batch 231

Batches 231 to 250 re-score, with `--kinds all`, the modules whose last full score was below 95%. They ran at the same time on one shared 4-core machine. While they ran, the kernel stopped 3 processes for lack of memory. A mutant whose test run is stopped that way counts as killed, so up to 3 kills across these batches may not be real. Batch 239 (`spark_builtins_sql.py`) is unused, because lane A's uncapped batch 187 scores all of it, and `sqlcompat/practices.py` is left to lane A's uncapped batch 224.

A re-score of `spark_number_format.py`, which batch 27 left at **96.0%** on point mutants and batch 39 at **66.7%** on boundary mutants, on today's `main`. This run counts both kinds together (`--kinds all`).

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. Each test set was run with `-x`. The machine was shared with three other mutation runs, so the per-mutant timeout was raised from 120 to 600 seconds for this batch (by setting `mutate.TIMEOUT` from a wrapper, with the tool unchanged); no mutant's run timed out. The module now has 284 mutants, so 150 were sampled. As in batch 27, the Spark SQL test files ran with `-k "number or to_char or to_varchar"` (with `or format_character` added for the new test in the "after" run), which leaves out `test_snowflake_sql.py::test_the_docs_table_is_the_codes`, the one that reads `docs/`.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_number_format.py` | test_spark_number_format_contract, test_spark_builtins_sql, test_spark_builtins_sql_edges, test_spark_builtins_sql_mutation, test_snowflake_sql, test_spark_number_format_mutation, with the `-k` filter above (+ test_spark_number_format_rescore) | 150 of 284 (145 + 5) | 142 / 8 | **94.7%** | 138/145 → 139/145 | 4/5 → 4/5 | 143 / 7 | **95.3%** | 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_number_format.py**:
  - Line 114, `token[1]` to `token[0]`: a format with a character that isn't a format character (`99x9`) must name that character in its error, not the token's kind.
  - → `test_spark_number_format_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_number_format.py` | 19 | `frozen=True` to `False` | Equivalent, as in batch 27: a digit run is never changed or hashed. |
| `spark_number_format.py` | 90 | `-1` to `-0` for no dollar sign | Equivalent, as in batch 27: it is only compared with positions, which are never negative. |
| `spark_number_format.py` | 92 | `first_digit < dollar` to `<=` | Equivalent, as in batch 39: a digit can't be at the dollar sign's index. |
| `spark_number_format.py` | 163 | `cap[i + 1:]` to `cap[i + 0:]` after a dollar sign | Equivalent: the dollar sign's group is `($?)`, so when it isn't `$` it is empty, and adding it to the rest changes nothing. |
| `spark_number_format.py` | 233 | `else 0` to `else 1` digits after the point | Equivalent, as in batch 27: with no decimal point, nothing reads them. |
| `spark_number_format.py` | 297, 298 | `or` to `and`, and the `"0"` fallback dropped | Not reached, as in batch 27: every valid format gives text other than a lone sign. |

