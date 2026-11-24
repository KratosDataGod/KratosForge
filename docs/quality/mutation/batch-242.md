# Mutation testing, batch 242

A re-score of `nbsymbols.py` (rename and go-to-definition across notebook cells) on today's `main`. Batch 27 scored it at **98.7%** before boundary mutants existed, and batch 36's boundary-only re-score left it at **59.1%**, with 9 equivalent. This run uses `--kinds all`, so both kinds are counted in one score.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module now has 294 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `nbsymbols.py` | test_nbsymbols, test_nbsymbols_cov2, test_nbsymbols_mutation, test_boundary_mutation (+ test_nbsymbols_rescore) | 150 of 294 (143 + 7) | 142 / 8 | **94.7%** | 137/143 → 139/143 | 5/7 → 5/7 | 144 / 6 | **96.0%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **nbsymbols.py**:
  - When two imports bind the same name on one line (`import os as p, collections as p`), the second one is found at its own `as`, not at the first one's.
  - An import alias counts once in how often a name is written, and other imports don't count for it.
  - → `test_nbsymbols_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `nbsymbols.py` | 27 | `[0, 0]` to `[1, 0]` | Equivalent, as in batch 27: lines count from 1, so the first entry is never read. |
| `nbsymbols.py` | 222 | `+ 6` to `+ 7` after `global` | Equivalent, as in batch 27: `global` is always followed by at least one space. |
| `nbsymbols.py` | 230, 235 | `0 < node.lineno` to `1 < node.lineno` | Equivalent: an `except` clause or a `case` pattern always comes after its `try` or `match` line, so it is never on line 1. |
| `nbsymbols.py` | 235, 273 | `0 < line` to `0 <= line` | Equivalent, as in batch 36: Python's line numbers start at 1, so a line is never 0. |

