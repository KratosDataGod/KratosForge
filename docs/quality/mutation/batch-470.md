# Mutation testing, batch 470

One module not scored before, run with `--kinds all`:
- The savings ledger's per-run accruals: one attribution result turned into ledger events shaped as `fdopt_savings` rows, with the run's gross, overhead and net checked, and the gate that writes nothing until a measured Fabric baseline exists (`savings/accruals.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 130 mutants, so all were run.

The test set was test_savings_accruals, test_savings_corrections, test_savings_corrections_mutation and test_savings_recurring.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/accruals.py` | test_savings_accruals, test_savings_corrections, test_savings_corrections_mutation, test_savings_recurring (+ test_savings_accruals_mutation_b) | 130 of 130 (124 + 6) | 110 / 20 | **84.6%** | 109/124 → 123/124 | 1/6 → 6/6 | 129 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/accruals.py**:
  - A result that isn't a dict is refused.
  - Each check refuses with its own reason: a baseline of an unknown kind, a Measured one without a figure and an Unknown one with a figure; CU avoided, or a gross alone, without a baseline; a gross that doesn't fit the layers; a gross without a net; a layer for another workload, or for another baseline version alone.
  - The net is complete only when the result says so, even with the overhead known.
  - The bounds at their tolerance: a gross from just below 0 to just over the baseline passes and one tolerance more is refused; the layers may add up to the baseline plus one tolerance per layer; a net from just below 0 to just over the gross passes.
  - Layers go in their order (0 before 1), then by name, and layers without an order go last.
  - An event keeps its layer's reason, and no reason is "".
  - → `test_savings_accruals_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/accruals.py` | 44 | `schema.expected(2)` to `expected(3)` for COLUMNS | Equivalent today: the latest schema version is 2, so `expected(3)` lists the same tables and columns. A version 3 that changes `fdopt_savings` would make it killable. |
