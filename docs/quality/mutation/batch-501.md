# Mutation testing, batch 501

One module not scored before, run with `--kinds all`:
- `NotSetUp`, the error for a step nobody has taken yet (no workspace assigned, no metadata project open, a lakehouse not promoted), so Monitoring and Data mesh Home decide by its type, not its wording (`errors.py`).

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason.

The test set was test_not_set_up and test_typed_errors.

The module is one class with a docstring and nothing else. `mutate.py` never changes docstrings, so it made no mutants, and there is nothing to score. No new tests were needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `errors.py` | test_not_set_up, test_typed_errors | 0 of 0 (0 + 0) | 0 / 0 | n/a | 0/0 → 0/0 | 0/0 → 0/0 | 0 / 0 | n/a | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

None: there were no mutants. No new tests needed.

### Left

None.
