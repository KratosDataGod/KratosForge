# Mutation testing, batch 236

A re-score of `spark_map.py`, which batch 40 left at **90.5%**, and of `spark_struct_fields.py`, which batch 10 left at **91.5%**, on today's `main`. Batch 10 ran before batch 36 added boundary mutants, so this run uses `--kinds all` for both.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Both modules have fewer than 150 mutants, so all were run. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. No mutant's run timed out.

Before the "after" run, the new test passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. It also passes on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_map.py` | test_spark_map, test_spark_map_mutation | 74 of 74 (72 + 2) | 67 / 7 | **90.5%** | 65/72 → 65/72 | 2/2 → 2/2 | 67 / 7 | **90.5%** | 7 equivalent |
| `spark_struct_fields.py` | test_spark_struct_fields, test_spark_struct_fields_edges, test_spark_struct_fields_mutation, test_spark_struct_fields_cov3 (+ test_spark_struct_fields_rescore) | 129 of 129 (129 + 0) | 121 / 8 | **93.8%** | 121/129 → 122/129 | none | 122 / 7 | **94.6%** | 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_struct_fields.py**:
  - Line 108, `m.group(1)` to `m.group(0)`: batch 10 could only kill it on DuckDB 2.0, which quotes every part of `col("s.c")`. DuckDB 1.5.6 also quotes a keyword, so `col("Order.c").withField(...)` reaches that line on both versions, and Spark names the column `update_fields(Order.c, ...)` with no quotes.
  - → `test_spark_struct_fields_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_map.py` | 62, 73 | `barrier=False` to `True` | Equivalent, as in batch 40: `barrier` is accepted for Spark's signature and not used on one PC. |
| `spark_map.py` | 70, 132 | `ignore_index=True` to `False` | Equivalent, as in batch 40: the joined frame's index isn't used. |
| `spark_map.py` | 90, 91 | `zip(..., strict=True)` to `strict=False` | Equivalent, as in batch 40: a batch's columns have the same length. |
| `spark_map.py` | 124 | the early return for an empty frame dropped | Equivalent, as in batch 40: an empty frame has no groups, so the same empty result comes back. |
| `spark_struct_fields.py` | 31, 36, 42, 48, 53 | `frozen=True` to `False` on the spec dataclasses | Equivalent, as in batch 10: nothing changes or hashes a spec after it is made. |
| `spark_struct_fields.py` | 107 | `and` to `or` before the dotted-path rewrite | Equivalent, as in batch 10: the name comes out the same either way. |
| `spark_struct_fields.py` | 198 | `or` to `and` before casting a constant | Equivalent, as in batch 10: it casts a constant to the type it already has. |

