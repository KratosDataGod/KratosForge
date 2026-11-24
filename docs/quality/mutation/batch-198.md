# Mutation testing, batch 198

`measurement.py`, run with `--kinds all` at `main` bdf2c177 (2026-11-19), with the earlier tests and the property tests. 147 mutants, so all of them ran.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `measurement.py` | test_measurement, test_measurement_mutation, test_optimizer_properties (+ test_optimizer_properties_mutation) | 147 of 147 (142 + 5) | 141 / 6 | **95.9%** | 137/142 → 141/142 | 4/5 → 5/5 | 146 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- A collection time given as a datetime is stored as ISO text.
- A period that starts and ends at the same moment is valid.
- A currency given for a CU total is ignored, not refused, and the total has no currency.
- → `test_optimizer_properties_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `measurement.py` | 57 | `_STRENGTH` unknown 2 → 3 | Equivalent, as in batch 43: only the order of the strengths matters. |

