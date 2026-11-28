# Mutation testing, batch 346

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Policy packages: a project's policies taken from the platform once, verified by their hashes, and resolved again offline (`central/policy_package.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902; the "after" run was at `main` 293fb38da, where the module is the same. The module has 147 mutants, and the run ran all 147 of 147 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_policy_package, test_policy_package_cov21 and test_policy_package_fuzz, about 16 seconds together. test_policy_promote_gate also calls the module, but it takes about a minute and uses it in one place, so it was left out; the score counts only the files given.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run's last kills each took 9 to 11 seconds, so the last four were checked by hand: each failed real tests (32, 1, 1 and 1). Three more kills, picked at random, were checked the same way: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (44, 29 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/policy_package.py` | test_policy_package, test_policy_package_cov21, test_policy_package_fuzz (+ test_policy_package_mutation) | 147 of 147 (141 + 6), uncapped | 138 / 9 | **93.9%** | 132/141 → 141/141 | 6/6 → 6/6 | 147 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/policy_package.py**:
  - A package is format 1, and format 1 is read.
  - A package missing parts names every one. A changed package shows the first 12 characters of both hashes, or "missing".
  - An assignment's settings are kept, and missing ones are empty. A resolved policy is kept even without an assignment on the chain.
  - Resolving offline filters by the context given.
  - The package hash ignores key order and writes text as ASCII.
  - → `test_policy_package_mutation.py`.

### Left

None.
