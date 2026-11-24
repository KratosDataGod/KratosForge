# Mutation testing, batch 209

A boundary re-score of three modules first scored before `mutate.py` made boundary mutants (`<` to `<=`, `>` to `>=`; see batch 36). Only the boundary mutants were run (`--kinds boundary`), so the scores below count those alone; each module's point score from its first batch stands as it was. The modules, with the batch that first scored each:
- DataFrame extras on this PC, among them `semanticHash` (`spark_df_extras.py`, a boundary re-score of batch 10).
- Publishing a table to OneLake (`publish.py`, a boundary re-score of batch 2).
- Paging through a REST API's results (`paging.py`, a boundary re-score of batch 5).

The run was at `main` bdf2c177 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran; each "after" run re-checked only the survivors, with `--only`. The tests given were each module's test files from its earlier batches, as paths relative to `sidecar/`, and they first passed once on the unmutated copy of `sidecar/`. No test needed `--deselect`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_df_extras.py` | test_spark_df_extras, test_spark_df_extras_edges, test_spark_df_extras_mutation (+ test_spark_df_extras_boundary) | 1 of 1 | 0 / 1 | **0.0%** | 1 / 0 | **100%** | none |
| `publish.py` | test_publish_edges, test_onelake_writes, test_lease_before_commit, test_table_leases, test_write_token, test_ea_write_integrity, test_publish_mutation, test_notebook_publish, test_publish_cooldown | 1 of 1 | 1 / 0 | **100%** | no change needed | **100%** | none |
| `paging.py` | test_paging, test_paging_complete, test_paging_mutation | 1 of 1 | 1 / 0 | **100%** | no change needed | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_df_extras.py**: `semanticHash` keeps a CRC of 2^31 - 1 and turns 2^31 and up negative, as a signed 32-bit int. → `test_spark_df_extras_boundary.py`.

### Left

None.

