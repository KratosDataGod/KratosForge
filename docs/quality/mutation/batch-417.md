# Mutation testing, batch 417

One module not scored before, run with `--kinds all`:
- The `kf_learner` package: the only KratosForge code a learner's PySpark process can import, built from the engine's own files, and the check that lists any import it doesn't allow (`academy/learner_pkg.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_pyspark_lab was left out of the test set because each of its runs takes about 166 seconds; that can only lower the "before" score. The module has 34 mutants, so all were run. `main` has changed this module since (#3188, #3247); the new tests pass against today's version.

The test set was test_trees, test_file_replace, test_academy_replace_where and test_academy_pyspark_streaming.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new import tests point `SOURCES` at a file the test wrote, so they don't depend on what the engine's files import.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/learner_pkg.py` | test_trees, test_file_replace, test_academy_replace_where, test_academy_pyspark_streaming (+ test_academy_learner_pkg_mutation_b) | 34 of 34 (33 + 1) | 30 / 4 | **88.2%** | 29/33 → 33/33 | 1/1 → 1/1 | 34 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/learner_pkg.py**:
  - A rewrite whose text is gone from its file is an error.
  - Only the standard library counts as standard.
  - The package can be built twice in the same place.
  - A lazy engine-only import is fine only inside a function. Another engine import is a problem even inside one.
  - → `test_academy_learner_pkg_mutation_b.py`.

### Left

None.
