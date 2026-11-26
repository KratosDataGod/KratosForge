# Mutation testing, batch 193

`savings/pricing.py`, versioned price scenarios and the value of avoided CU, run the same way as batch 191 (111 mutants, all run).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/pricing.py` | as batch 191 (+ test_savings_properties, test_savings_pricing_mutation) | 111 of 111 | 101 / 10 | **91.0%** | 110 / 1 | **99.1%** | 1 equivalent |

No bugs were found by the mutants. The order gap in `value_runs` is in the properties section above.

### What the survivors were, and the tests that now kill them

- **savings/pricing.py**:
  - A version keeps its rate to six places and its source trimmed, and has a 16-digit id.
  - A version that ends when it starts is refused, and an overlap names the scenario.
  - Zero and fractional CU are priced, and a negative figure is refused.
  - Runs are priced on net by default, and on gross when asked.
  - → `test_savings_pricing_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/pricing.py` | 177 | the "more than one currency" raise removed in `value_runs` | Equivalent: every price comes from one scenario, and `check` refuses a scenario in more than one currency before any run is priced, so the line can't be reached. |

