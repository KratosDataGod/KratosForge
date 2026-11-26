# Mutation testing, batch 201

`opportunities/analyzers.py`, run with `--kinds all` at `main` bdf2c177, with the earlier tests and the property tests. It has 191 mutants, so 150 were sampled (seed 7).

`optimize/shared.py` was looked at as well. It computes no figure: it stores the measurements and recommendations it is given, under stable ids. So batches 202 to 204 are unused.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `opportunities/analyzers.py` | test_opportunities, test_opportunities_analyzers_mutation, test_optimizer_properties (+ test_optimizer_properties_mutation) | 150 of 191 (144 + 6) | 145 / 5 | **96.7%** | 139/144 → 141/144 | 6/6 → 6/6 | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- A whole-table rewrite is quoted to 120 characters.
- Copies with no known lakehouse name their code by a 16-character fingerprint.
- → `test_optimizer_properties_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `opportunities/analyzers.py` | 147 | `same[0]` → `same[1]` for the tables read and written | Equivalent: every notebook in the group has the same code, so the same tables. |
| `opportunities/analyzers.py` | 270 | `sort_keys=True` → `False` | Equivalent, as in batch 25: `_canon` already builds every dictionary in key order. |
| `opportunities/analyzers.py` | 375 | `scheduled=True` → `False` | Equivalent, as in batch 25: only NB007 is switched on, and it isn't one of the scheduled-only rules. |

