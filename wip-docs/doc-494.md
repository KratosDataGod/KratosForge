# Mutation testing, batch 494

One module not scored before, run with `--kinds all`:
- The Fabric provider's discovery: the workspaces a person can open, and in one workspace each lakehouse that is a KratosForge environment (by its manifest), one that can be adopted (it has `fdopt_schema_version` but no manifest), or one that can't be read, with the reason. A workspace is cut at 200 lakehouses, and the caller is told (`controlplane/fabric.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 21 mutants, so all were run.

The test set was test_controlplane.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivors with `--only`. Fabric and OneLake are fakes; nothing reaches a tenant.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `controlplane/fabric.py` | test_controlplane (+ test_controlplane_fabric_mutation_b) | 21 of 21 (20 + 1) | 15 / 6 | **71.4%** | 15/20 → 19/20 | 0/1 → 1/1 | 20 / 1 | **95.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **controlplane/fabric.py**:
  - The cut is at 200 lakehouses. A workspace with exactly 200 is read whole and isn't marked as cut short.
  - A new discovery hasn't cut anything short before it is asked.
  - A lakehouse with no manifest whose tables can't be listed is reported as unreadable: "No access" when it is denied, and "Couldn't be read (...)" with the error otherwise.
  - → `test_controlplane_fabric_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `controlplane/fabric.py` | 71 | the sort rank of "unreadable" from 2 to 3 | Equivalent: the ranks only order the states, and 3 still sorts after 0 and 1. |
