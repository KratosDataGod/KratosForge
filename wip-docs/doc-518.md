# Mutation testing, batch 518

A re-score of `central/udf.py`, the UDF_Platform function app and the definition files it is uploaded as. Batch 17 left it at **92.7%**, and batch 245 at **92.7%** (kinds all; the module has no boundary mutants), each with 3 equivalent mutants. Batch 170 found nothing to re-score.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged; no mutant's run timed out. Each pytest session got a fresh temporary folder. The module has 41 mutants, so all of them ran. Batch 245 used a smaller set because batch 17's took over 4 minutes then and the tool stopped a run at 120 s. Batch 17's whole set now takes about 60 s, so it was used again, with batch 245's test_central_udf_rescore. The files first passed once on the unmutated copy, and none of them reads `docs/`, `examples/` or `app/`, so nothing was left out.

The test set was test_udf_parity, test_central_udf_mutation, test_central_udf_rescore, test_requests_core_four, test_platform_setup_steps, test_platform_retries, test_platform_refusals, test_platform, test_best_practice_checks, test_product_groups, test_trust_badges, test_shared_keys and test_governance.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/udf.py` | test_udf_parity, test_central_udf_mutation, test_central_udf_rescore, test_requests_core_four, test_platform_setup_steps, test_platform_retries, test_platform_refusals, test_platform, test_best_practice_checks, test_product_groups, test_trust_badges, test_shared_keys, test_governance | 41 of 41 (41 + 0) | 38 / 3 | **92.7%** | 38/41 → 38/41 | none | 38 / 3 | **92.7%** | 3 equivalent |

No bugs were found. No new tests were needed: each survivor is one that batches 17 and 245 found equivalent.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/udf.py` | 1018, 1019 | `indent=2` to `3` in the definition's JSON | Equivalent, as batches 17 and 245 said: the JSON reads back the same, and nothing compares the text. |
| `central/udf.py` | 925 | `"-" * 60` to `61` in a separator comment | Equivalent, as batches 17 and 245 said: the line is a comment in the generated code. |
