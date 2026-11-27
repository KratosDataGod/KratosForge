# Mutation testing, batch 343

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The mastery class catalog: classes, their specializations and talent trees, the skills registry, and the checks that refuse a bad catalog (`mastery/catalog.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 125 mutants, and the run ran all 125 of 125 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `mastery/catalog.py`: test_mastery and test_mastery_catalog_cov3. Together they take about 2 seconds, so none was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests: two stopped test_mastery from loading at all, because it reads the shipped catalog when it is collected, and the third failed 1 test.

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `mastery/catalog.py` | test_mastery, test_mastery_catalog_cov3 (+ test_mastery_catalog_mutation_a) | 125 of 125 (118 + 7), uncapped | 116 / 9 | **92.8%** | 110/118 → 117/118 | 6/7 → 7/7 | 124 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **mastery/catalog.py**:
  - `CatalogError` counts only the problems past five: five give no "(and N more)", six give "(and 1 more)".
  - A class title and an achievement name may not name a god.
  - `known_ids` names what hooks may point at, Learn drafts included.
  - `load` checks that hooks name real content unless told not to.
  - → `test_mastery_catalog_mutation_a.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `mastery/catalog.py` | 254 | `lru_cache(maxsize=4)` to 5 | Equivalent: the cache size changes only how many loaded catalogs are kept, never what `load` returns. |

**Main changed after this run (2026-11-27):** #3229 (class achievements earned from a rule) changed `mastery/catalog.py` after the run. The score above is for the module at the run's `main`, as named on this page, and it was not run again on the new module. When main was merged into the PR, the tests were updated to match: the test file's small catalog now gives each specialization a capstone talent and each achievement a `requires`, which #3229's checks ask for.
