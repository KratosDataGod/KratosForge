# Mutation testing, batch 191

`savings/attribution.py`, the attribution waterfall, run with `--kinds all` on `main` bdf2c177 (2026-11-19). The method is the same as batch 95: sample cap 150, seed 7, test files given relative to `sidecar/` and passing once on the unmutated copy first. The module has 112 mutants, so all of them ran. "Before" uses the tests that were there. "After" adds `test_savings_properties.py` and re-runs the survivors with `--only`. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/attribution.py` | test_savings_attribution, test_savings_attribution_mutation, test_savings_recurring, test_savings_pricing, test_measurement (+ test_savings_properties) | 112 of 112 | 110 / 2 | **98.2%** | 110 / 2 | **98.2%** | 2 equivalent |

No bugs were found by the mutants. The rounding gap is in the properties section above.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/attribution.py` | 58 | `ms.UNKNOWN: 2` to `3` in `_STRENGTH` | Equivalent: only the order of the three strengths is used, and it stays the same. |
| `savings/attribution.py` | 218 | `complete = False` to `True` when the overhead is unknown | Equivalent: the result's `complete` is `complete and o is not None`, and `o` is None on that branch. |

