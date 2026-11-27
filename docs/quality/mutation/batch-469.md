# Mutation testing, batch 469

One module not scored before, run with `--kinds all`:
- The KratosForge Environment's provider contracts: what each provider can do and the evidence for it, which provider setup offers and which is Preview, and the small records a plan and discovery pass around (`controlplane/providers.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 48 mutants, all of them point mutants, so all were run.

The test set was test_controlplane and test_controlplane_service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `controlplane/providers.py` | test_controlplane, test_controlplane_service (+ test_controlplane_providers_mutation_b) | 48 of 48 (48 + 0) | 38 / 10 | **79.2%** | 38/48 → 48/48 | none | 48 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **controlplane/providers.py**:
  - A capability level or evidence outside the lists is refused, each with its own message.
  - Every contract record (Capability, ProviderDescriptor, StoreRef, Found, PlanStep, ProvisionPlan) is frozen, so a plan the person saw can't be changed in place.
  - Databricks and Snowflake have no adapter; Fabric and this PC do. Their status alone didn't show it, since a provider that claims nothing is not available either way.
  - → `test_controlplane_providers_mutation_b.py`.

### Left

None. Every mutant is killed.
