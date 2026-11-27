# Mutation testing, batch 413

One module not scored before, run with `--kinds all`:
- The Academy's workspace reader: the only way a check reads what a learner left in an exercise folder, with no links, no paths outside the folder, and size, file and row limits (`academy/workspace.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_sandbox was left out of the test set because each of its runs takes about 65 seconds; that can only lower the "before" score. The module has 136 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_validators, test_academy_validators_mutation_b, test_academy_lab_python and test_academy_pylab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The Windows paths are tested by setting `_WINDOWS` and replacing `os.open` or `os.fstat`. The two tests that make symbolic links skip where the account can't make them.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/workspace.py` | test_academy_validators, test_academy_validators_mutation_b, test_academy_lab_python, test_academy_pylab (+ test_academy_workspace_mutation_b) | 136 of 136 (129 + 7) | 90 / 46 | **66.2%** | 88/129 → 126/129 | 2/7 → 7/7 | 133 / 3 | **97.8%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/workspace.py**:
  - The limits are what the docs say. A JSON file, a file, a table file, a folder and a table of exactly the limit are read, and one more is refused. The messages name the limit in MB.
  - A name-surrogate reparse point is a link. A OneDrive placeholder and a compressed file aren't.
  - On Windows, a file swapped between the two looks counts as linked, the same file gives its own link count, and a file that can't be opened is refused.
  - A pack folder that links elsewhere isn't an exercise. A link inside a listed folder is refused.
  - A name that is empty, has a NUL, is over 400 characters or isn't text is refused, and 400 characters is fine. A name with no parts or a parent step is refused.
  - A folder read as a file, a file listed as a folder, a path through a file, a folder with no files of the format and a missing exercise folder are each refused in words.
  - → `test_academy_workspace_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/workspace.py` | 46 | `st_reparse_tag` default 0 to 1 | Equivalent: Windows stat results have the attribute, and 1 has no bit in common with the name-surrogate bit. |
| `academy/workspace.py` | 55 | `O_BINARY` default 0 to 1 | Equivalent: the line runs only on Windows, where `os` has `O_BINARY`. |
| `academy/workspace.py` | 55 | `O_NOINHERIT` default 0 to 1 | Equivalent: the line runs only on Windows, where `os` has `O_NOINHERIT`. |
