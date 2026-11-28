# Mutation testing, batch 510

A re-score of `warehouse_compare.py` on today's `main`, with `--kinds all`. It compares a warehouse project's files with the warehouse and makes the deploy plan from that. It was last scored in batch 31 at **98.0%** (point mutants only, before batch 36 added boundary mutants), and its boundary mutants alone in batch 164 at **88.9%**.

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 342 mutants, so a sample of 150 was run.

The test set was the files of batches 31 and 164: test_warehouse_compare_more_edges, test_warehouse_precondition, test_warehouse_project, test_warehouse_compare_mutation and test_warehouse_compare_boundary.

Before the "after" run, the new test passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. It also passes on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `warehouse_compare.py` | test_warehouse_compare_more_edges, test_warehouse_precondition, test_warehouse_project, test_warehouse_compare_mutation, test_warehouse_compare_boundary (+ test_warehouse_compare_mutation_b) | 150 of 342 (147 + 3) | 147 / 3 | **98.0%** | 144/147 → 145/147 | 3/3 → 3/3 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **warehouse_compare.py**: the side-by-side view of a script over 200 lines whose lines repeat (such as `GO` between batches) still lines up line by line. difflib's `autojunk` would treat those lines as junk and show the whole script as one changed block. → `test_warehouse_compare_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `warehouse_compare.py` | 66 | `t[:2]` to `t[:3]` in the `N'...'` check | Equivalent, as batch 31 said: a plain `'` string is caught first, and an `N'...'` token is never a keyword, so it doesn't fold either way. |
| `warehouse_compare.py` | 66 | the `or` of the string check to `and`, so a string is marked as a word | Equivalent, as batch 31 said: a quoted string is never in `RESERVED` or `SOFT`, so it doesn't fold. |
