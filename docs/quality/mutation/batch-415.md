# Mutation testing, batch 415

One module not scored before, run with `--kinds all`:
- The Academy's Python and Delta exercises: the checks on what a learner's script left in its workspace, and grading (`academy/pylab.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 75 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_pylab and test_academy_limits.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests run no learner script: the checks read files the test wrote, and `grade()` gets a stand-in for the learner process.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pylab.py` | test_academy_pylab, test_academy_limits (+ test_academy_pylab_mutation_b) | 75 of 75 (75 + 0) | 60 / 15 | **80.0%** | 60/75 → 75/75 | none | 75 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pylab.py**:
  - A delta_lab exercise can't run in an AppContainer.
  - A Python exercise can't run another kind of check, and says so.
  - A settings file the script wrote is checked.
  - A table is named after its file: cut to 40 characters, with a letter first when the name starts with a digit, and "answer" when the path has no file name.
  - A missing table file is a fail in words. A file DuckDB can't read is named with 200 characters of the error. An error line is cut to 300 characters.
  - grade() keeps the last 20,000 characters of the script's output and of its errors.
  - → `test_academy_pylab_mutation_b.py`.

### Left

None.
