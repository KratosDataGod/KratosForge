# Mutation testing, batch 246

A re-score of `intellisense.py` (signature help and cell syntax checks) and `b64.py` (strict base64) on today's `main`. Batch 60 left `intellisense.py` at **94.7%** and batch 76 left `b64.py` at **94.1%**, every mutant left equivalent. Both runs already counted boundary mutants. This run checks them against today's code with `--kinds all`.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. `intellisense.py` now has 187 mutants, so 150 were sampled. `b64.py` has 17, and all were run. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. Five `intellisense.py` mutants made a loop never end, and they count as killed, as in batch 60.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `intellisense.py` | test_intellisense, test_intellisense_edges, test_intellisense_mutation (+ test_intellisense_rescore) | 150 of 187 (139 + 11) | 143 / 7 (5 timeouts counted as killed) | **95.3%** | 134/139 → 135/139 | 9/11 → 10/11 | 145 / 5 (5 timeouts counted as killed) | **96.7%** | 5 equivalent |
| `b64.py` | test_b64, test_b64_mutation | 17 of 17 (16 + 1) | 16 / 1 | **94.1%** | 15/16 → 15/16 | 1/1 → 1/1 | 16 / 1 | **94.1%** | 1 equivalent |

No bugs were found. `b64.py` needed no new tests.

### What the survivors were, and the tests that now kill them

- **intellisense.py**:
  - An indented `%%python` line is dropped, not kept as an indented statement, so the error is on the right line.
  - A cell of only blank lines has no problem.
  - → `test_intellisense_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `intellisense.py` | 87 | `nl < 0` to `nl < 1` | Equivalent, as in batch 60: the search for the end of a comment starts at the `#`, so it can't find a line break at index 0. |
| `intellisense.py` | 181 (2) | `len(stripped) - 4` to `+ 4` or `- 5` | Equivalent, as in batch 60: these change only the spaces after `pass` at the end of a line. |
| `intellisense.py` | 197 | `max(..., 0)` to `max(..., 1)` for the end column | Equivalent: the end column can be 0 only when Python ends the error at offset 1, and that happens only on the error's own line, where the start column takes over. A search of short two- and three-line cells found no error that ends at offset 1 on a later line. |
| `intellisense.py` | 198 | `end_line > err_line` to `>=` with no end offset | Equivalent, as in batch 60: with no end offset, the end column is already one past the start. |
| `b64.py` | 20 | `pad < len(text) - 2` to `- 3` | Equivalent, as in batch 76: the next test refuses that text either way. |

