# Mutation testing, batch 170

A boundary re-score, as in batch 163, of the last two modules in this lane with boundary mutants: `benchmark.py` (batch 16) and `git_service.py` (batch 19). The run was at `main` 4ddd3662 (2026-11-18) with batches 163 to 169's tests added, and the same method as batch 163; `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` reads files outside `sidecar/` and was left out.

`git_service.py`'s one mutant survived the five test files that name the module, but `test_git_service_platform_mutation.py` (batch 19's own test, which uses it through fixtures) already kills it: exactly five ignored files are named with no "and more". No new tests were needed.

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `benchmark.py` | test_benchmark_mutation, test_receipts_env, test_side_by_side | 1 of 1 | 1 / 0 | **100%** | 1 / 0 | **100%** | none |
| `git_service.py` | the 5 test files that use it (+ test_git_service_platform_mutation) | 1 of 1 | 0 / 1 | **0.0%** | 1 / 0 | **100%** | none |

No bugs were found.

These modules in the lane have no boundary mutants on today's `main`, so there was nothing to re-score: `engines_service.py` (batch 15), `central/udf.py` (batch 17), `snowflake_sql_parity.py` (batch 18), `meta/templates.py` and `optimize_service.py` (batch 19), `meta/diff.py` (batch 29), `orchestration/validate.py` (batch 32), `meta/lineage.py` and `sqlcompat/profiles.py` (batch 34).

