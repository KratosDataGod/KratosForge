# Mutation testing, batch 243

A re-score of `sqlcompat/analyzer.py` (the compatibility report's statement splitting) on today's `main`. Batch 32 left it at **94.9%**, before boundary mutants existed, and batch 164 boundary re-scored it (**85.7%**, all left equivalent). This run uses `--kinds all`. `sqlcompat/practices.py` was planned here too, but lane A's uncapped batch 224 scores all of its 596 mutants, so it was left to that batch.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. `analyzer.py` now has 175 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sqlcompat/analyzer.py` | test_sql_compat, test_sqlcompat_analyzer_mutation, test_sqlcompat_analyzer_boundary, test_sqlcompat_rules_mutation, test_sqlcompat_rules_uncapped, test_tsql_contract, test_sql_practices, test_learn_fixes, test_learn_fixes_edges, test_learn_labs, test_platforms_mutation, test_boundary_mutation_3 (+ test_sqlcompat_analyzer_rescore) | 150 of 175 (143 + 7) | 139 / 11 | **92.7%** | 133/143 → 134/143 | 6/7 → 6/7 | 140 / 10 | **93.3%** | 9 equivalent, 1 unreachable (#2674) |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **sqlcompat/analyzer.py**:
  - A parse error placed on line 0 has no place in the text.
  - → `test_sqlcompat_analyzer_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sqlcompat/analyzer.py` | 185 | `return int(error["start"])` to `None` | Unreachable, as in batch 32: the supported sqlglot versions' errors have no `start` (#2674). |
| `sqlcompat/analyzer.py` | 90 | `go_line_end = -1` to `0` | Equivalent: no token starts before 0, so nothing is skipped either way. |
| `sqlcompat/analyzer.py` | 92 | `t.start < go_line_end` to `<=` | Equivalent, as in batch 164: no token starts at the newline or the end of the script. |
| `sqlcompat/analyzer.py` | 96 | `t.token_type == VAR` to `!=` | Equivalent, as in batch 32: sqlglot's T-SQL tokenizer makes every `go` a `COMMAND` token. |
| `sqlcompat/analyzer.py` | 99, 133 (2 each) | `find` from `t.end - 1` or `t.end` | Equivalent, as in batch 32: both places are inside the `GO` token, which has no newline. |
| `sqlcompat/analyzer.py` | 100 | `end == -1` to `end == 0` | Equivalent: the search starts after `GO`, so it never finds a newline at 0. |
| `sqlcompat/analyzer.py` | 132 | `rfind` from 1 instead of 0 | Equivalent, as in batch 32: it only misses a newline at 0, and the line is stripped before it is compared. |

