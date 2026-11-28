# Mutation testing, batch 513

A re-score of `notebook_files.py` on today's `main`, with `--kinds all`. It opens `.ipynb` files as the app's notebooks, keeps what was published to each Fabric item, and keeps deleted notebooks in Recently deleted. It was last scored in batch 9 at **96.7%** (point mutants only), and its boundary mutants alone in batch 158 at **93.3%**.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 252 mutants, so a sample of 150 was run.

The test set was the files of batches 9 and 158: test_notebook_files, test_notebook_files_edges, test_notebook_files_service_edges, test_cloud_service_more_edges, test_deep_nesting, test_notebook_folders, test_notebook_mutation, test_notebook_batch_edges, test_notebook_formats, test_notebook_formats_service_edges, test_notebook_resources, test_kernel_sessions, test_notebook_files_mutation and test_notebook_files_boundary.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_files.py` | the 14 files of batches 9 and 158 (+ test_notebook_files_mutation_b) | 150 of 252 (143 + 7) | 143 / 7 | **95.3%** | 137/143 → 140/143 | 6/7 → 6/7 | 146 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **notebook_files.py**:
  - A cell of another type (raw) is numbered among the cells that are kept: Cell 1 when the setup cell before it is left out, Cell 3 after two other cells.
  - Each Fabric item keeps its own published copy, in a file named by its id, and an id that isn't one is refused.
  - → `test_notebook_files_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebook_files.py` | 431, 432 | a restored entry's `unlink(missing_ok=True)` to `False` | Equivalent, as batch 9 said: each file was read just before. |
| `notebook_files.py` | 370 | `dest.unlink(missing_ok=True)` to `False` when a deleted notebook's copy reads back wrong | Equivalent, as batch 9 said: the file was written just before. |
| `notebook_files.py` | 50 | `len(text) <= LINE_CHARS` to `<` in `_snippet` | Equivalent, as batch 158 said: a line of exactly LINE_CHARS is cut from 0 to LINE_CHARS, with no "…" either side, which is the same line. |
