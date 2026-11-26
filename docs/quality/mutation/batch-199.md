# Mutation testing, batch 199

`calibration.py`, run with `--kinds all` at `main` bdf2c177, with the earlier tests and the property tests. 65 mutants, so all of them ran.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `calibration.py` | test_calibration, test_calibration_mutation, test_optimizer_properties | 65 of 65 (60 + 5) | 64 / 1 | **98.5%** | 59/60 → 59/60 | 5/5 → 5/5 | 64 / 1 | **98.5%** | 1 equivalent |

No bugs were found, and no new tests were needed.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `calibration.py` | 26 | `return False` → `return None` | Equivalent, as in batch 38: `_usable` is only a filter, where None is as false as False. |

