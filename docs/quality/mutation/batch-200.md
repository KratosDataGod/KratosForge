# Mutation testing, batch 200

`opportunities/impact.py`, run with `--kinds all` at `main` bdf2c177, with the earlier tests and the property tests. 68 mutants, so all of them ran.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `opportunities/impact.py` | test_opportunities, test_opportunities_impact_mutation, test_optimizer_properties | 68 of 68 (68 + 0) | 65 / 3 | **95.6%** | 65/68 → 65/68 | none | 65 / 3 | **95.6%** | 3 equivalent |

No bugs were found, and no new tests were needed.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `opportunities/impact.py` | 49 | `start is None or end is None` → `and` | Equivalent, as in batch 40: `end` falls back to `start`, so it is None only when `start` is. |
| `opportunities/impact.py` | 89, 114 | `zip(..., strict=True)` → `strict=False` | Equivalent: both lists are built from the same workloads, so they are always the same length. |

