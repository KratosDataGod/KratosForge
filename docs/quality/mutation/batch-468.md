# Mutation testing, batch 468

One module not scored before, run with `--kinds all`:
- The Excellence assessment's DevOps controls, from Fabric's Git connection and Git status for a workspace: not connected to Git (FEA-DEVOPS-003), changes not committed (FEA-DEVOPS-004) and behind its branch (FEA-DEVOPS-005), with the evidence record that keeps only the state, provider, branch and counts (`excellence/catalog_devops.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 97 mutants, so all were run.

The test set was test_excellence_devops, test_excellence_catalog and test_excellence_hardening.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The machine ran short of memory once during the "after" run, so it was run a second time; both runs killed all ten.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_devops.py` | test_excellence_devops, test_excellence_catalog, test_excellence_hardening (+ test_excellence_catalog_devops_mutation_b) | 97 of 97 (96 + 1) | 87 / 10 | **89.7%** | 87/96 → 96/96 | 0/1 → 1/1 | 97 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_devops.py**:
  - Text from Fabric is cut at 256 characters. A space and characters from 128 up are kept; a character below 32, or DEL (127), makes it empty.
  - The record names the workspace by its display name, or by `name` when that is all there is. Its source says Get Status only when the status was kept, and a branch never synced keeps none.
  - A provider the app doesn't name is shown as Fabric gives it. No provider and no branch reads "a Git branch".
  - Conflicts are mentioned only when there are some, for uncommitted and for incoming changes.
  - → `test_excellence_catalog_devops_mutation_b.py`.

### Left

None. Every mutant is killed.
