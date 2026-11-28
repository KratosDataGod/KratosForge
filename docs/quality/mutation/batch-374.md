# Mutation testing, batch 374

One module not scored before, run with `--kinds all`:
- Entra ID groups for a domain: the suggested group names, the plan of groups each role needs, and the PowerShell, Azure CLI, CSV and Fabric-role scripts made from it (`central/entra.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. Of `test_csv_cells.py` only `test_the_listing_exports_protect_and_count` was run, the one test there that reaches this module: the whole file takes over a minute a run. The module has 137 mutants, so all were run; none is a boundary mutant. `main` hasn't changed this module since.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/entra.py` | test_entra_service_mutation, test_entra_groups, test_entra_graph, test_csv_cells (one test) (+ test_central_entra_mutation) | 137 of 137 (137 + 0) | 114 / 23 | **83.2%** | 114/137 → 137/137 | none | 137 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/entra.py**:
  - A role can't be changed once made.
  - The domain part of a group name is the domain's id unless the naming says `name`. A name is cut to 256 characters and a mail nickname to 64.
  - A domain that isn't there is refused. A naming without `roles_off` plans every role.
  - A domain without a name is called by its id. The environment's words go into a description only for a role given per environment. A domain's Fabric domain is kept, and one without is empty.
  - Only new groups with names a script can't carry are left for by hand; a linked group never is.
  - The header names the first row's domain (none for no rows), who made it only when known, and "1 new group" or "2 new groups".
  - The CSV leaves a linked group's mail nickname empty.
  - The roles script names each Fabric role: a domain role waiting for the domain to be in Fabric, a domain role given, a workspace role with no workspace known yet, and the readers' by-hand step. A role with no Fabric role (stewards) adds nothing.
  - → `test_central_entra_mutation.py`.
