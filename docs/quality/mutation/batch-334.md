# Mutation testing, batch 334

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Best practice checks: the registry of checkers that governance policies point at, and the built-in naming, ownership, classification and contract checkers (`governance_checks.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 222 mutants, and the run ran all 222 of 222 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `governance_checks.py`: test_model_practices, test_best_practice_checks, test_best_practice_checkers_cov2, test_best_practice_checkers and test_checks_service_cov2. Together they take about 7 seconds, so none was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (24, 2 and 2).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `governance_checks.py` | test_model_practices, test_best_practice_checks, test_best_practice_checkers_cov2, test_best_practice_checkers, test_checks_service_cov2 (+ test_governance_checks_mutation) | 222 of 222 (217 + 5), uncapped | 180 / 42 | **81.1%** | 180/217 → 217/217 | 0/5 → 5/5 | 222 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **governance_checks.py**:
  - `register` returns the checker, and `checkers` lists it. A rule with a severity that isn't Error, Warning or Info is refused, and so is a rule missing a field, by its id and the field.
  - A rule no checker has is reported missing. A disabled rule checks and passes nothing. Settings without `enabled` run the rule.
  - A checker that fails reports Unknown for the item it was checking, by its kind and id.
  - A finding links the rule's own page, or the docs when the rule has none.
  - Naming checks only entities. A table name of exactly 256 characters is checked and one of 257 isn't, and the message shows its first 40. A pattern of exactly 300 characters is used and one of 301 isn't. A name of exactly `max_length` is allowed.
  - The entity owner rule applies only when it is on, and flags only a missing owner. A product needs a steward unless the policy says otherwise.
  - "Tables are classified" applies only in its layers (any case), and everywhere when no layers are given.
  - Contract checks only published products, and only rules that are on. Freshness allows 24 hours by default, a load exactly at the limit is fresh, a time without a zone is read as UTC, and the fix opens the first entity. Row count without a minimum allows zero, and exactly the minimum passes.
  - → `test_governance_checks_mutation.py`.

### Left

None.
