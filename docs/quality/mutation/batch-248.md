# Mutation testing, batch 248

A re-score of `excellence/findings.py`, which batch 110 left at **92.3%**, on today's `main`, to see whether anything new survives.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 26 mutants, all point mutants, so all were run. The tests were batch 110's set, given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/findings.py` | test_excellence, test_excellence_catalog, test_excellence_findings_mutation | 26 of 26 (26 + 0) | 24 / 2 | **92.3%** | 24/26 → 24/26 | none | 24 / 2 | **92.3%** | 2 equivalent |

No bugs were found. No new tests were needed: the two survivors are the two that batch 110 found equivalent.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/findings.py` | 22 | effort and risk `"high": 2` to `3` | Equivalent, as in batch 110: only the order is used, and 3 is still above medium's 1. |
| `excellence/findings.py` | 23 | evidence `"insufficient": 3` to `4` | Equivalent, as in batch 110: only the order is used, and 4 is still above inferred's 2. |

