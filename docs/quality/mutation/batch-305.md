# Mutation testing, batch 305

One module that had never been scored before: `notebook_sync.py`. It ran with `--kinds all`. The module has no `<`, `<=`, `>` or `>=` comparisons, so there are no boundary mutants, and every mutant ran.
- Merging a notebook that was changed both in KratosForge and in another program, such as VS Code, before a sync. Cells are matched by their cell id against the cells both sides last agreed on. A change made on one side is taken as it is. A cell changed on both sides, deleted on one side and changed on the other, or moved on both sides is a named conflict that carries both versions. The bases, which are the cells kept on disk by the file's stamp, are part of the module too.

The run was at `main` c9716c45d (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (`--cap 150 --seed 7 --kinds all`). The module has 140 mutants and all of them ran. Test files were given to the tool as paths relative to `sidecar/` and ran with `-n 2` (`-n 3` after), because `test_notebook_sync_conflicts.py` merges notebooks of a thousand cells and takes about a minute alone. The tests passed once on the unmutated copy first. Each pytest session got a new temporary folder, removed when the session ended, and no folder 40 or more levels deep was left. No `-k` filter was used. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was deselected. No mutant's run timed out.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, so the run can't score a false 100%. The new tests pass on DuckDB 2.0.0 and on sqlglot 30.23.0 and 30.27.0 too.

One survivor of the "after" run, the cell settings mutant on line 45, was then killed by a test added after it, and it was run again on its own with `--only`. Adding a test can't revive a killed mutant, so that re-run gives the final count.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_sync.py` | test_notebook_sync_conflicts (+ test_notebook_sync_mut305) | 140 of 140 (140 + 0) | 117 / 23 | **83.6%** | 117/140 → 136/140 | none | 136 / 4 | **97.1%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **notebook_sync.py**:
  - A change to a cell's settings alone, such as its title, is a change, whether the cell had settings before or not.
  - A cell id that isn't text is refused.
  - A cell the other side added at the top stays at the top. One it added after the notebook's first cell goes right after that cell.
  - When both sides moved the cells the same way, KratosForge's order leads, and the other side's new cell goes right after the cell it follows there.
  - The second copy of a cell kept both ways is named after it: `-theirs`, then `-theirs2` and so on. Only letters, digits, `_` and `-` are kept, at most 40 of them, or `cell` when none are left. When every numbered name up to 999 is taken, a random 16-character id is used. The copy is titled where it came from.
  - A notebook's bases are kept in a folder named by the first 24 characters of its name's hash.
  - A base kept for another notebook, or whose cells aren't a list, or that isn't a record at all, is no base.
  - These tests are in `test_notebook_sync_mut305.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebook_sync.py` | 72 | `range(n - 1, ...)` to `range(n - 0, ...)` when looking back for the cell a new cell follows | Equivalent: the extra position looked at is the new cell itself, which isn't placed yet, so it is passed over. |
| `notebook_sync.py` | 215 | `ensure_ascii=False` to `True` when a base is written | Equivalent: it only changes how non-ASCII text is escaped in the file, and reading it back gives the same cells. |
| `notebook_sync.py` | 223 | `missing_ok=True` to `False` when old bases are pruned | Equivalent: the files pruned were just listed, so each one is there to delete. |
| `notebook_sync.py` | 242 | `missing_ok=True` to `False` when a notebook's bases are forgotten | Equivalent: the same, for the files `forget` has just listed. |
