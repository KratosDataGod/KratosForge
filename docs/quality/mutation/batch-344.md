# Mutation testing, batch 344

One module, scored for the first time in mutation lane A. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The first excellence controls: workspace admins, guests, apps and direct writers, the workspace description, a running capacity, and the optimization engine's duplicate and full-refresh findings (`excellence/catalog.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 116 mutants, and the run ran all 116 of 116 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that imports `excellence/catalog.py`: test_catalog_artifacts_cov3, test_excellence_architecture, test_excellence_artifacts, test_excellence_capacity, test_excellence_catalog, test_excellence_devops and test_excellence_models. Together they take about 5 seconds. test_excellence_ui_names also imports it, but its one test reads `app/ui/optimize-excellence.js`, which the tool's copy of `sidecar/` doesn't have, so that file was left out. No `-k` filter or `--deselect` was needed.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (3, 1 and 3).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog.py` | test_catalog_artifacts_cov3, test_excellence_architecture, test_excellence_artifacts, test_excellence_capacity, test_excellence_catalog, test_excellence_devops, test_excellence_models (+ test_excellence_catalog_mutation) | 116 of 116 (112 + 4), uncapped | 94 / 22 | **81.0%** | 92/112 → 112/112 | 2/4 → 4/4 | 116 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

Most survivors were the words of a control's message: one or more, "is" or "are". The new tests run each check on a stand-in context that returns one evidence record, and read its message exactly.

- **excellence/catalog.py**:
  - Names are listed five at a time, then "and N more"; so are the peers that couldn't be read.
  - Admin, guest, app and direct-writer counts read as words for 0, 1 and 2; an unknown guest is "this is a guest" or "they are guests".
  - Only users with a write role count as direct writers: groups and viewers don't.
  - An active capacity names its size, or "size not given".
  - → `test_excellence_catalog_mutation_a.py`.

### Left

None.

Batch 343's section first said its run was at `main` 293fb38da; it was at 447143424, as this batch's was. That line is corrected.

Lane B scored the same module at the same time, in batch 362 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 362's page each report their own run. Neither is a re-score of the other.
