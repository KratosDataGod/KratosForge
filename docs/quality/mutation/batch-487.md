# Mutation testing, batch 487

One module not scored before, run with `--kinds all`:
- Adopting an existing install as a KratosForge Environment, the compatibility state of every store, and the one status the app shows for its environment (CP-006 to CP-008) (`controlplane/health.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 70 mutants, so all were run. The "after" figure is a run of the survivors again (`--only`) with the new test file added. Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

The test set was test_controlplane, test_controlplane_service_mutation_b.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `controlplane/health.py` | test_controlplane, test_controlplane_service_mutation_b (+ test_controlplane_health_mutation_b) | 70 of 70 (69 + 1) | 66 / 4 | **94.3%** | 66/69 → 69/69 | 0/1 → 0/1 | 69 / 1 | **98.6%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **controlplane/health.py**:
  - An adopted Platform keeps its display name, and is called `SQL_Platform` when it has none or an empty one.
  - One result waiting is said in the singular, two in the plural. A sync kind that reports no pending count has none waiting.
  - → `test_controlplane_health_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `controlplane/health.py` | 89 | `operational_version > app_operational_version` to `>=` | Equivalent: equal versions were already taken as ready just before. |
