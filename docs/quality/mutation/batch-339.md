# Mutation testing, batch 339

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- One Fabric Excellence Assessment run, its snapshot, rescoring and comparison (`excellence/assess.py`, FEA-104).

The run was at `main` 062d120bb (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 78 of 78 mutants ran (78 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is the seven files that call the module: test_excellence, test_excellence_architecture, test_excellence_artifacts, test_excellence_capacity, test_excellence_catalog, test_excellence_devops and test_excellence_models, about 7 s a session. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/assess.py` | test_excellence, test_excellence_architecture, test_excellence_artifacts, test_excellence_capacity, test_excellence_catalog, test_excellence_devops, test_excellence_models (+ test_excellence_assess_mutation) | 78 of 78 (78 + 0) | 62 / 16 | **79.5%** | 62/78 → 78/78 | none | 78 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/assess.py**:
  - The counts by severity of the scored findings, with the profile raising one to critical and another to high.
  - A snapshot isn't a fixture unless asked, and its policy version and fingerprint are 16 characters.
  - Inventory entries that aren't objects, or lack an id or a type, are skipped.
  - An item not inspected gives the reason its control couldn't decide, and "No control could decide." when none gave one.
  - A workspace's score is the same assessed alone or beside another, and it counts its items (fixing a notebook raises it).
  - compare gives each change to a tenth, and None where either side has none.
  - → `test_excellence_assess_mutation.py`.
