# Mutation testing, batch 357

Two modules not scored before, run with `--kinds all`:
- The semantic model controls from a model's definition: relationships that filter both ways, row-level security, Direct Lake fallback and the definition-only model rules (`excellence/catalog_models.py`).
- The notebook and pipeline controls: the notebook rules and the pipelines' deactivated activities and fixed workspace ids (`excellence/catalog_artifacts.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. Each module has 79 mutants, so all were run.

Since ea988e99, #3162 added pipeline retry, timeout and failure-path controls to `catalog_artifacts.py`; this score is of the module as it was at ea988e99. The new tests also pass against it as it is on `main` today.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_models.py` | test_excellence_catalog, test_excellence_models, test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_capacity, test_excellence_architecture (+ test_excellence_catalog_models_mutation) | 79 of 79 (77 + 2) | 54 / 25 | **68.4%** | 53/77 → 77/77 | 1/2 → 2/2 | 79 / 0 | **100%** | none |
| `excellence/catalog_artifacts.py` | the same (+ test_excellence_catalog_artifacts_mutation) | 79 of 79 (77 + 2) | 46 / 33 | **58.2%** | 46/77 → 77/77 | 0/2 → 2/2 | 79 / 0 | **100%** | none |

No bugs were found. The existing tests check these controls through whole assessments, by outcome; most survivors changed only a control's text, a count or a default.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_models.py**:
  - Names list five and count the rest; one and many are worded apart.
  - A payload counts as read only with a model kind and a relationship count.
  - Every control says not evaluated, with its reason, when the definition wasn't read, and a rule without a rules object is not evaluated.
  - The exact texts of the both-directions, row security and fallback controls, and DirectQuery only named as such.
  - Fallback applies from one Direct Lake table. A rule hit without a count is 0 found.
  - → `test_excellence_catalog_models_mutation.py`.
- **excellence/catalog_artifacts.py**:
  - Places and names list five and count the rest.
  - A notebook rule's text for one place and many, a hit without a count, and a clean notebook's cell count (0 when not given).
  - The VACUUM control names 7 days.
  - An activity that couldn't be read is named once or many; the deactivated and fixed workspace texts and details for one and many.
  - → `test_excellence_catalog_artifacts_mutation.py`.

### Left

None.
