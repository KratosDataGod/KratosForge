# Mutation testing, batch 466

One module not scored before, run with `--kinds all`:
- Creating a KratosForge Environment in Fabric: the plan that says what would be made and what each step needs, the confirmed apply that makes the workspace, lakehouse, tables and manifest in that order, and the plan and apply that upgrade an environment's shared tables (`controlplane/provisioning.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_controlplane_hardening was left out of the test set because it adds about 30 seconds to every mutant's run; that can only lower the "before" score. The module has 142 mutants, all of them point mutants, so all were run.

The test set was test_controlplane and test_controlplane_service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. Nothing here talks to Fabric: the tests use test_controlplane's fake Fabric and lakehouses in a folder. Two survivors were left by the first "after" run. They got tests and were run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `controlplane/provisioning.py` | test_controlplane, test_controlplane_service (+ test_controlplane_provisioning_mutation_b) | 142 of 142 (142 + 0) | 90 / 52 | **63.4%** | 90/142 → 142/142 | none | 142 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **controlplane/provisioning.py**:
  - Every plan step says whether it creates something. A new workspace, lakehouse, family's tables and the manifest do. A refusal, a workspace or lakehouse found, and a family that is ready do not.
  - A name or owner the manifest would refuse blocks the plan at its manifest step.
  - An existing lakehouse whose manifest is damaged, that can't be read, or that you aren't allowed to read gives a blocked step with that reason. A family there that is newer is a refusal. A family that needs its tables says nothing about other PCs.
  - A plan that was blocked when it was shown is refused with its own reason, even when the name has since come free.
  - Each apply step says what it did ("Created KratosForge", "Wrote the manifest"). A new lakehouse is opened with schemas; an existing one is opened to look. A lake that doesn't say whether it has schemas is recorded with them.
  - A failed workspace stops before the lakehouse, and a failed family stops before the manifest. A manifest that can't be written is `ok` False, not None.
  - A family whose setup gives no version is recorded at its latest version.
  - The upgrade plan: its steps and request, "nothing to upgrade" when every family is ready, a refusal for a newer manifest, a newer family, a lakehouse that can't be read and one you can't read. A store without a schemas flag is opened with schemas.
  - The upgrade apply: it needs a confirm, the same environment, a manifest that is still there, a plan that isn't now blocked and the same steps. It keeps the version of a family it left alone and the rest of the manifest's schema part. A failed family stops before the manifest, which stays as it was.
  - → `test_controlplane_provisioning_mutation_b.py`.

### Left

None. Every mutant is killed.
