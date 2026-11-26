# Mutation testing, batch 359

One module not scored before, run with `--kinds all`:
- The architecture controls: naming evidence (environment, layers, left-overs), lakehouse properties, notebook bindings, and the medallion, schemas, left-over and binding controls (`excellence/catalog_architecture.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 133 mutants, so all were run.

Since ea988e99, #3165 changed two lines of the module; this score is of it as it was at ea988e99. The new tests also pass against it as it is on `main` today.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_architecture.py` | test_excellence_catalog, test_excellence_models, test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_capacity, test_excellence_architecture (+ test_excellence_catalog_architecture_mutation) | 133 of 133 (130 + 3) | 103 / 30 | **77.4%** | 101/130 → 130/130 | 2/3 → 3/3 | 133 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_architecture.py**:
  - The workspace subject uses its id and its display name or name. Naming skips an item without a type.
  - A lakehouse's record takes its own name and its default schema. Bindings are only for notebooks whose definition was read or not read. Properties are read only for lakehouses.
  - Names list five and count the rest, falling back to the id.
  - The exact medallion texts: one unnamed lakehouse, one or two mixed, and a split layer with its detail. Schemas name the default schema or dbo.
  - The left-over control's not evaluated texts (both words, or none), and one and many left-overs.
  - A missing binding, or one without its field, is unknown; the binding control needs both records.
  - → `test_excellence_catalog_architecture_mutation.py`.

### Left

None.
