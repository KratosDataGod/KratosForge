# Mutation testing, batch 462

One module not scored before, run with `--kinds all`:
- Versioned baselines and the before and after check that verifies an optimization: a workload's median Fabric CU per run from its comparable runs in a window, with stable ids and versions that supersede, the CU change between two figures, and the Verified, Validating, Regressed or Failed result of an optimization's after runs (`savings/baselines.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. test_perf_compare was left out because it reads `examples/benchmark/`, which isn't in the copy of `sidecar/`; that can only lower the "before" score. The module has 247 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_savings_baselines, test_savings_links, test_calibration, test_calibration_comparison and test_calibration_mutation.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the 23 survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/baselines.py` | test_savings_baselines, test_savings_links, test_calibration, test_calibration_comparison, test_calibration_mutation (+ test_savings_baselines_mutation_b) | 150 of 247 (143 + 7) | 127 / 23 | **84.7%** | 125/143 → 142/143 | 2/7 → 7/7 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/baselines.py**:
  - A CU figure of an unknown classification is refused. A run without its run_id is refused. validate refuses a min_runs of 0, below 0 or a Boolean.
  - Zero CU and a fraction of a CU are valid figures.
  - A window that ends where it starts is refused. A min_runs of 1 uses a single run. A run at exactly the window start counts.
  - Baseline ids, versions, figure ids, after-figure ids and validation ids have 16 characters after their prefix.
  - A measured baseline names its runs' sources, and falls back to "Fabric runs of this workload" when they name none. An estimated baseline keeps its source and its window as its period.
  - A validation is dated at its latest after run.
  - An unknown change says which side isn't known. A measured change names both sources and takes the after figure's collection time.
  - No change is a reduction of zero, not an increase. The change is rounded to 6 places.
  - → `test_savings_baselines_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/baselines.py` | 60 | `_STRENGTH` unknown `2` to `3` | Equivalent: the values only order the three classes for `max`, and unknown stays the weakest. |
