# Mutation testing, batch 97

One module not covered before, not touched by an open pull request, run with `--kinds all`, so its score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. It has no boundary mutants. The table shows both parts.
- Local T-SQL's common table expressions: refusing a recursive CTE, `MAXRECURSION`, two CTEs of one name and a change through a CTE, and noting a nested CTE (`localdata/tsql_ctes.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. The module has fewer than 150 mutants, so all of them ran. The test set was run with `-x`, so a mutant's run stops at its first failing test.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so the run below can't score a false 100%. No `-k` filter or `--deselect` was used, since neither test file reads `docs/`, `examples/` or `app/`.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql_ctes.py` | test_tsql_ctes (+ test_localdata_tsql_ctes_mutation) | 54 of 54 (54 + 0) | 51 / 3 | **94.4%** | 51/54 → 52/54 | none | 52 / 2 | **96.3%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/tsql_ctes.py**:
  - An UPDATE whose target is a table's alias in FROM, with a CTE joined beside it, changes the table and runs. Only an alias of a CTE itself makes the target a change through a CTE, which is still refused.
  - → `test_localdata_tsql_ctes_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/tsql_ctes.py` | 62 | `return False` → `return None` | Equivalent: the caller only tests it for truth. |
| `localdata/tsql_ctes.py` | 73 | `return False` → `return None` | Equivalent: the caller only tests it for truth. |

