# Mutation testing, batch 245

A re-score of `central/udf.py` (the UDF_Platform function app and its definition files) on today's `main`. Batch 17 left it at **92.7%**, with 3 equivalent. That was before boundary mutants existed, but the module has none, so this run's 41 mutants are the same kinds.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. All 41 mutants were run. Batch 17's test set now takes over 4 minutes, and the tool stops a run at 120 s, so a smaller set was used: the files that build or read the definition (test_udf_parity, test_central_udf_mutation, test_requests_core_four, test_platform_setup_steps). They first passed once on the unmutated copy, and none reads `docs/`, `examples/` or `app/`. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/udf.py` | test_udf_parity, test_central_udf_mutation, test_requests_core_four, test_platform_setup_steps (+ test_central_udf_rescore) | 41 of 41 (41 + 0) | 31 / 10 | **75.6%** | 31/41 → 38/41 | none | 38 / 3 | **92.7%** | 3 equivalent |

No bugs were found. The 7 new survivors were killed in batch 17 by the larger set. They are now killed by the smaller set too, so a re-run fits in the time limit.

### What the survivors were, and the tests that now kill them

- **central/udf.py**:
  - Each function binds the SQL database (`FabricSqlConnection` with its alias) and, when it writes, the caller's context. Only the caller's own arguments are listed as function parameters, with their types.
  - The return type is the function's own, and helper functions (`_run`) aren't listed.
  - Every listed function has its public endpoint on.
  - → `test_central_udf_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/udf.py` | 1018, 1019 | `indent=2` to `3` in the definition's JSON | Equivalent, as in batch 17: the JSON reads back the same, and nothing compares the text. |
| `central/udf.py` | 925 | `"-" * 60` to `61` in a separator comment | Equivalent, as in batch 17: the line is a comment in the generated code. |

