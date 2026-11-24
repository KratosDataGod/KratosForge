# Mutation testing, batch 247

A re-score of two Learn modules on today's `main`:
- `learn/fixes.py`, which batch 57 left at **92.0%** with 12 equivalent mutants.
- `learn/guides.py`, which batch 116 left at **92.9%** with 4 mutants of its build script not killed.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. `learn/fixes.py` has 261 mutants, so 150 were sampled; `learn/guides.py` has 56, so all were run. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. `test_learn_guides.py::test_the_shipped_guides_are_the_docs` reads `docs/`, so it was deselected from the `learn/guides.py` runs, as in batch 116. `test_academy_guide_check.py` runs a tool against `docs/` too, so it was left out. No mutant's run timed out.

Before the "after" run, the new test passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. It also passes on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `learn/fixes.py` | test_learn_fixes, test_learn_fixes_edges, test_learn_fixes_mutation | 150 of 261 (138 + 12) | 138 / 12 | **92.0%** | 130/138 → 130/138 | 8/12 → 8/12 | 138 / 12 | **92.0%** | 12 equivalent |
| `learn/guides.py` | test_learn_guides, test_learn_guides_mutation (+ test_learn_guides_rescore) | 56 of 56 (54 + 2) | 52 / 4 | **92.9%** | 50/54 → 54/54 | 2/2 → 2/2 | 56 / 0 | **100.0%** | none |

No bugs were found. The `learn/fixes.py` sample is the same as batch 57's, and its 12 survivors are the 12 that batch 57 found equivalent, so it needed no new tests.

### What the survivors were, and the tests that now kill them

- **learn/guides.py**:
  - The build script, run by hand as `python -m fabric_desktop.learn.guides ../docs`, writes the guides of the docs folder it is given as indented JSON that keeps non-ASCII text as it is and ends in a newline. Batch 116 left these four mutants because a test would rewrite the shipped `rule_guides.json`. The test runs a copy of the module from a temporary folder, so the copy writes its own `rule_guides.json` there.
  - → `test_learn_guides_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `learn/fixes.py` | 89 | `return -1` to `return None` | Equivalent, as in batch 57: no token starts at -1 or None, so the finding gets no fix either way. |
| `learn/fixes.py` | 88 | `col < 1` to `col < 0` | Equivalent, as in batch 57: column 0 points at the line break before the line or before the query, where no token starts. |
| `learn/fixes.py` | 108 | `_close(s, i + 1)` to `_close(s, i)` | Equivalent, as in batch 57: `YEAR` and its `(` have the same depth, so the same `)` is found. |
| `learn/fixes.py` | 189 | two moves in the bracketed-schema branch | Equivalent, as in batch 57: SQ005 only fixes names in `dbo`, which needs no brackets, so the branch never runs. |
| `learn/fixes.py` | 203 | `len(toks) > 2` to `> 3` and to `>= 2` | Equivalent, as in batch 57: `CREATE VIEW` or `CREATE VIEW v` has no `AS`, so Compare is not offered either way. |
| `learn/fixes.py` | 210 | `t.start >= start` to `>` | Equivalent, as in batch 57: the token at `start` is `SELECT` or `WITH`, never a write. |
| `learn/fixes.py` | 213, 214 | the closing-semicolon check moved or negated | Equivalent, as in batch 57: the statement splitter leaves the `;` out of a statement's tokens, so there is never one to trim. |
| `learn/fixes.py` | 215 | `start <= fix start` to `<`, and `fix start < fix end` to `<=` | Equivalent, as in batch 57: a fix never starts on the `SELECT` or `WITH` the read starts at, and is never empty. |

