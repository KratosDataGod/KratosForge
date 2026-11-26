# Mutation testing, batch 481

One module not scored before, run with `--kinds all`:
- Optimization demand changes as inputs for the capacity planner (ESI-602): per capacity and per workload, the CU demand change as two inputs, verified then estimated, never added together. Regressions count as increases, an unknown change stays unknown, nothing is ever enough to right-size a capacity, and layers that aren't an optimization's, failed optimizations and runs with no capacity are listed apart with a reason (`savings/planner_inputs.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 215 mutants, so a sample of 150 was run.

The test set was test_savings_planner_inputs.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/planner_inputs.py` | test_savings_planner_inputs (+ test_savings_planner_inputs_mutation_b) | 150 of 215 (145 + 5) | 117 / 33 | **78.0%** | 117/145 → 145/145 | 0/5 → 3/5 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/planner_inputs.py**:
  - Optimizations that aren't a list, or whose links aren't rows, are refused.
  - A validation is refused, in its own words, when it has no row or change, no optimization_id, a result that isn't one, no time, a change of another class, a known change without a figure, or a figure that isn't a number.
  - A Verified change of exactly the tolerance is no change, not refused.
  - An event of no named optimization says "none". An accrual below 1 CU hour is still an accrual.
  - Of two validations at one time with one result, the first is kept.
  - A per-run increase is one past the tolerance whatever the result, and a Regressed one is an increase. An unknown per-run change gives the change's reason, then the validation's, then the default.
  - Totals round to 6 places and count Measured and Estimated events. A verified input with no events is Unknown.
  - A layer that isn't an optimization's is named by its attribution.
  - A validation without runs is placed by the workload its baseline names, or else listed as unplaced because it doesn't name one. A Regressed one without runs is listed as a regression.
  - → `test_savings_planner_inputs_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/planner_inputs.py` | 251 | `cu_hours > 0` to `>= 0` in the reduction sum | Equivalent: a 0 adds 0. |
| `savings/planner_inputs.py` | 252 | `cu_hours < 0` to `<= 0` in the increase sum | Equivalent: a 0 adds 0. |
