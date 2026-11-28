# Mutation testing, batch 472

One module not scored before, run with `--kinds all`:
- Accepted risk on the Excellence assessment: exceptions with an owner, a reason and an expiry, checked before use, and shown on the failed outcomes and findings they match without changing any outcome, score or flag (`excellence/exceptions.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 91 mutants, so all were run.

The test set was test_excellence_exceptions.

Before the "after" run, the new test passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. It also passes on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/exceptions.py` | test_excellence_exceptions (+ test_excellence_exceptions_mutation_b) | 91 of 91 (85 + 6) | 90 / 1 | **98.9%** | 85/85 → 85/85 | 5/6 → 6/6 | 91 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/exceptions.py**:
  - Exactly 1,000 exceptions are allowed, and one more is refused. The existing test only tried 1,001.
  - → `test_excellence_exceptions_mutation_b.py`.

### Left

None. Every mutant is killed.
