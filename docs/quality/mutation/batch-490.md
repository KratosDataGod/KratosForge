# Mutation testing, batch 490

One module not scored before, run with `--kinds all`:
- What the KratosForge Environment itself costs, kept apart from what it saves: measured sync overhead per period, Fabric capacity and money left unknown, and net savings only when both sides are known and comparable (CP-011) (`controlplane/cost.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 55 mutants, so all were run. The "after" figure is a run of the survivors again (`--only`) with the new test file added. Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

The test set was test_controlplane_cost, test_controlplane_hardening.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `controlplane/cost.py` | test_controlplane_cost, test_controlplane_hardening (+ test_controlplane_cost_mutation_b) | 55 of 55 (52 + 3) | 42 / 13 | **76.4%** | 41/52 → 52/52 | 1/3 → 3/3 | 55 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **controlplane/cost.py**:
  - The period includes its first and last moment, is 30 days unless said, and ends now when no time is given.
  - A row time given as a datetime counts, with or without a zone (none is UTC). A time in another zone, as a datetime or as text, is read in that zone. A time that is neither text nor a datetime is skipped.
  - Each row's source, method and assumptions are kept in the total.
  - Net savings are rounded to six places.
  - → `test_controlplane_cost_mutation_b.py`.

### Left

Nothing is left: every mutant is killed.
