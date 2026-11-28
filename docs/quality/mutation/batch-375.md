# Mutation testing, batch 375

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The notebook and pipeline controls, decided from the definitions the optimization engine already reads (`excellence/catalog_artifacts.py`, FEA-301 and FEA-302).

The run was at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 128 of 128 mutants ran (126 point + 2 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_catalog and test_excellence, about 6 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_artifacts.py` | test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_catalog, test_excellence (+ test_excellence_artifacts_mutation) | 128 of 128 (126 + 2) | 87 / 41 | **68.0%** | 87/126 → 126/126 | 0/2 → 2/2 | 128 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_artifacts.py**:
  - Most survivors were wording no test read whole: the singular and plural of place, activity, has and have, for one item and for two.
  - Places and activity names: five are shown and the rest counted ("and 2 more"), with none counted at exactly five; activities that couldn't be read show five names.
  - The VACUUM threshold is 7 days, in the pass and the fail text.
  - A rule hit with no count says 0 places, and a notebook with no cell count says 0 code cells were checked.
  - The deactivated and fixed-workspace findings keep the activity names in their detail.
  - Retry, timeout and failure path are not evaluated when the inspection didn't keep that setting, even with a count of activities that call out.
  - → `test_excellence_artifacts_mutation.py`.
