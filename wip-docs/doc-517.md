# Mutation testing, batch 517

A re-score of `preview.py`, which gives the first rows of a Delta table or a data file, the start of a text file and a notebook's cells. Batch 54 left it at **94.7%**, and batch 154 at **94.7%** (kinds all), each with 4 equivalent mutants. Since then the module reads a JSON file that isn't JSON Lines value by value, and gives pyarrow only a file whose first value is an object, so it has grown from 76 mutants to 91.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged; no mutant's run timed out. Each pytest session got a fresh temporary folder. The module has 91 mutants, so all of them ran. The test set was batch 154's: test_preview, test_preview_mutation and test_preview_rescore. They first passed once on the unmutated copy, and none of them reads `docs/`, `examples/` or `app/`, so nothing was left out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `preview.py` | test_preview, test_preview_mutation, test_preview_rescore (+ test_preview_mutation_b) | 91 of 91 (89 + 2) | 76 / 15 | **83.5%** | 74/89 → 85/89 | 2/2 → 2/2 | 87 / 4 | **95.6%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **preview.py**:
  - A JSON Lines file that starts with an object is read by pyarrow, so a date-time text gets pyarrow's `timestamp[s]` type.
  - Only the first 4,096 bytes are looked at: an object after 4,096 bytes of blank lines isn't given to pyarrow, and its text stays a string.
  - A file of values, one a line, passes over blank lines.
  - A first line that isn't JSON gives the error for the file as one document. A later one is named by its number ("Line 3 of the file isn't JSON").
  - → `test_preview_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `preview.py` | 46, 64, 65, 76 | `zip(strict=True)` to `False`, `flatten=True` to `False`, the pyarrow conversion's check negated, and the file name split with `rsplit("/", 0)` | Equivalent, as batches 54 and 154 said (batch 154 had them on the same lines). |
