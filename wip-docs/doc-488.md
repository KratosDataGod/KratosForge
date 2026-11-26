# Mutation testing, batch 488

One module not scored before, run with `--kinds all`:
- What to optimize next: open opportunities ranked in tiers (measured, estimated, run time, scenario, unknown) by the benefit their evidence shows (ESI-603) (`savings/priorities.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 76 mutants, so all were run. The "after" figure is a run of the survivors again (`--only`) with the new test file added. Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

The test set was test_savings_priorities.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `savings/priorities.py` | test_savings_priorities (+ test_savings_priorities_mutation_b) | 76 of 76 (75 + 1) | 73 / 3 | **96.1%** | 73/75 → 75/75 | 0/1 → 1/1 | 76 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **savings/priorities.py**:
  - A measured benefit of zero is a figure and is ranked as measured.
  - The benefit is rounded to six places (1 CU second a month is 0.000278 CU hours).
  - An impact that isn't a measurement is refused with an error that names the opportunity.
  - → `test_savings_priorities_mutation_b.py`.

### Left

Nothing is left: every mutant is killed.
