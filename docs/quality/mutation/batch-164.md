# Mutation testing, batch 164

A boundary re-score, as in batch 163, of six more modules first scored before batch 36. Only the boundary mutants were run (`--kinds boundary`); each module's point score from its first batch stands. The modules, with the batch that first scored each:
- Comparing a warehouse with its project, and the deploy plan made from that (`warehouse_compare.py`, batch 31).
- Skins: the colors, rail and bar a skin may set, and their contrast (`skins.py`, batch 24).
- The optimization opportunities found in a workspace's items (`opportunities/analyzers.py`, batch 25).
- Notebook magics: how a cell's lines are read, and Spark SQL split into statements (`magics.py`, batch 33).
- The SQL compatibility check: splitting a script and placing a parse error (`sqlcompat/analyzer.py`, batch 32).
- agent.toml as the agent reads it (`agent_config.py`, batch 24).

The run was at `main` 4ddd3662 (2026-11-18) with batch 163's tests added, and the same method as batch 163: every test file that uses the module, as paths relative to `sidecar/`, first passing once on the unmutated copy, and every boundary mutant of each module. Tests that read files outside `sidecar/` were left out: six tests in `test_skins.py` and two in `test_parity.py` for `skins.py`, `test_spark_decimal_rules.py::test_what_still_differs_is_listed_in_the_compatibility_docs` for `sqlcompat/analyzer.py`, and eight tests in `test_mailbox.py` and `test_agent_queue_bound.py` for `agent_config.py`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse_compare.py` | the 5 test files that use it (+ test_warehouse_compare_boundary) | 9 of 9 | 7 / 2 | **77.8%** | 8 / 1 | **88.9%** | 1 equivalent |
| `skins.py` | test_parity, test_skins, test_skins_mutation (+ test_skins_boundary) | 8 of 8 | 6 / 2 | **75.0%** | 7 / 1 | **87.5%** | 1 equivalent |
| `opportunities/analyzers.py` | test_opportunities, test_opportunities_analyzers_mutation | 8 of 8 | 8 / 0 | **100%** | 8 / 0 | **100%** | none |
| `magics.py` | test_notebook_magics, test_notebook_magics_mutation, test_notebook_magics_windows, test_opportunities_analyzers_mutation (+ test_notebook_magics_boundary) | 8 of 8 | 4 / 4 | **50.0%** | 6 / 2 | **75.0%** | 2 equivalent |
| `sqlcompat/analyzer.py` | the 5 test files that use it (+ test_sqlcompat_analyzer_boundary) | 7 of 7 | 4 / 3 | **57.1%** | 6 / 1 | **85.7%** | 1 equivalent |
| `agent_config.py` | the 24 test files that use it (+ test_agent_config_boundary) | 7 of 7 | 4 / 3 | **57.1%** | 7 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **warehouse_compare.py**: one new column is named "column" and two "columns" in the deploy plan. → `test_warehouse_compare_boundary.py`.
- **skins.py**: a color pair with exactly the minimum contrast, 4.5:1, is readable. → `test_skins_boundary.py`.
- **magics.py**: a string left open at the end of its line ends with the line, and a word that ends at the end of its line ends there. → `test_notebook_magics_boundary.py`.
- **sqlcompat/analyzer.py**: a parse error at the first character is placed there, and one just past the end isn't placed. → `test_sqlcompat_analyzer_boundary.py`.
- **agent_config.py**: a schedule every 1 minute and every `MAX_EVERY_MINUTES` is read, 0 and one more are refused, and a shared schedule every 1 minute is read. → `test_agent_config_boundary.py`.

`opportunities/analyzers.py` had no survivors.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `warehouse_compare.py` | 261 | `set(w) <= set(p)` to `<` | Equivalent: when the two tables have the same columns, the next checks give None too, as a changed type or order, or no column added. |
| `skins.py` | 140 | `c <= 0.04045` to `<` | Equivalent: a channel is a whole number over 255, which is never exactly 0.04045. |
| `magics.py` | 213 | `i < 0` to `<=` after looking for the end of a `--` comment | Equivalent: the search starts at the comment's own `-`, so it finds a later newline or none (-1), never 0. |
| `magics.py` | 218 | `end < 0` to `<=` after looking for `*/` | Equivalent: the search starts two characters in, so it finds 2 or more, or -1, never 0. |
| `sqlcompat/analyzer.py` | 92 | `t.start < go_line_end` to `<=` | Equivalent: `go_line_end` is the place of a newline or the end of the script, where no token starts. |

