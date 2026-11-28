# Mutation testing, batch 349

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The semantic model controls, decided from the model's definition only (`excellence/catalog_models.py`, FEA-304).

The run was at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 79 of 79 mutants ran (77 point + 2 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_excellence_models, test_excellence_catalog and test_excellence, about 5 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_models.py` | test_excellence_models, test_excellence_catalog, test_excellence (+ test_excellence_models_mutation) | 79 of 79 (77 + 2) | 54 / 25 | **68.4%** | 53/77 → 77/77 | 1/2 → 2/2 | 79 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_models.py**:
  - In an assessment a definition that wasn't read never reaches a control, so each control's own "not evaluated" answer (for a payload that isn't a read model, or has no rule results) was never checked. They are now called directly.
  - Names: five are listed and the rest counted ("and 2 more"), with none counted at exactly five.
  - Singular and plural wording for one relationship, role and Direct Lake table.
  - Row security names the tables its roles filter, and says "no security role" when roles are missing or 0.
  - Direct Lake fallback applies with one table or more, and DirectQuery only and Automatic each fail with their own text and detail; a missing table count is 0.
  - A matched rule with no count says 0.
  - → `test_excellence_models_mutation.py`.
