# Mutation testing, batch 377

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The architecture controls and the evidence they read: names, lakehouse properties and notebook bindings (`excellence/catalog_architecture.py`, FEA-203).

The run was at `main` 293fb38da (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 133 of 133 mutants ran (130 point + 3 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_excellence_architecture, test_excellence_catalog and test_excellence, about 5 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_architecture.py` | test_excellence_architecture, test_excellence_catalog, test_excellence (+ test_excellence_architecture_mutation) | 133 of 133 (130 + 3) | 103 / 30 | **77.4%** | 101/130 → 130/130 | 2/3 → 3/3 | 133 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_architecture.py**:
  - Names: five shown and the rest counted, none counted at exactly five, and a row without a name named by its id.
  - The workspace is named by `name` when it has no display name, and a lakehouse by the read's display name when the item list has none.
  - An item without an id or a type is skipped; only Lakehouse items with an id are read as lakehouses, and only notebooks make binding records. A lakehouse's default schema is kept.
  - Wording: 0, 1 and 2 lakehouse names; one and two lakehouses named for two layers; a layer split with no mixed lakehouse; one and two left-over items; the words an unclear environment says; dbo as the default schema.
  - `_bound` is unknown without a binding, or one with no default lakehouse, and `_binding` asserts it has both its binding and the inventory.
  - → `test_excellence_architecture_mutation.py`.
