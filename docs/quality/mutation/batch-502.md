# Mutation testing, batch 502

One module not scored before, run with `--kinds all`:
- File names built from names people chose (a lakehouse, a query, a report): only letters, digits, spaces, `-` and `_` are kept, at most 60 characters, with a default when nothing is left (`filenames.py`).

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 6 mutants, so all were run.

The test set was test_notebook_formats_service_mutation, test_notebook_formats_service_edges, test_notebook_formats and test_checks_service_cov2. No test called `file_stem` directly; these reach it through the notebook export and the checks project file.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivor with `--only`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `filenames.py` | test_notebook_formats_service_mutation, test_notebook_formats_service_edges, test_notebook_formats, test_checks_service_cov2 (+ test_filenames_mutation_b) | 6 of 6 (6 + 0) | 5 / 1 | **83.3%** | 5/6 → 6/6 | 0/0 → 0/0 | 6 / 0 | **100.0%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **filenames.py**:
  - A long name keeps exactly 60 characters, and the cut comes after the unsafe characters are dropped. No test had a name that long, so 61 passed.
  - → `test_filenames_mutation_b.py`.

### Left

None.
