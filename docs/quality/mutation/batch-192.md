# Mutation testing, batch 192

`savings/recurring.py`, period figures and the monthly projection, run the same way as batch 191 (88 mutants, all run).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/recurring.py` | as batch 191 (+ test_savings_properties, test_savings_recurring_mutation) | 88 of 88 | 79 / 9 | **89.8%** | 85 / 3 | **96.6%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/recurring.py**:
  - A period with a measured run and an estimated one is estimated, whichever comes first.
  - A window of one day projects, and a window of 0 days is refused.
  - A rebaselined projection has no runs of its own.
  - The per-run figure is rounded to six places.
  - → `test_savings_recurring_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/recurring.py` | 36 | `ms.UNKNOWN: 2` to `3` in `_STRENGTH` | Equivalent: only the order of the strengths is used. |
| `savings/recurring.py` | 88, 90 | `round(..., 6)` to `7` for a period's gross and net | Equivalent: each run's figure is already rounded to 6 places, so their sum differs from 6 places only by float noise far below 5e-8. |

