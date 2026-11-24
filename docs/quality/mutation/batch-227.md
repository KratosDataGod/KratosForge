# Mutation testing, batch 227

One module, run uncapped: every one of its mutants, not a sample of 150.
- Line magics and Spark SQL in notebook cells: how a cell's lines are read, and Spark SQL split into statements (`magics.py`).

Batch 33 sampled 150 of this module's mutants then, 221 in all (86.7% before, 98.0% after), and batch 164 ran its 8 boundary mutants (50.0% before, 75.0% after). The module has 240 mutants now. This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 240 of 240, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` 8d93c5070 (2026-11-23), merged into the branch first, with `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy. Each pytest run had a temporary folder of its own, made fresh by a local plugin and removed when the run ended, so another run's leftover files could not fail a mutant and no run left files behind. Three "killed" mutants picked at random (106, 222 and 184) were run again by hand, and each failed a real assertion.

Batches 33 and 164 ran test_notebook_magics, test_notebook_magics_mutation, test_notebook_magics_windows, test_opportunities_analyzers_mutation and test_notebook_magics_boundary. The run here adds the other test files that call the module: test_notebook_formats_edges, test_notebook_formats, test_databricks_magics, test_notebook_practices and test_code_format. The score counts only the files given. The set runs in about 15 seconds, so no file was left out for time. `test_databricks_notebook_helpers.py` reads `docs/`, so it was left out, as in batch 33.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Nine mutants timed out, before and after alike: each stops a scanning loop from moving on (`i += 1` to `i += 0`, `i = j + 1` to `j - 1`, `i + 3` to `i - 3`, `j += ... else 1` to `else 0`, and two negated conditions, at lines 62 and 211, that send the scan down a branch that can stay where it is), so the tests hang on them and they count as killed. Each was then run again on its own with a longer limit: eight hang the module's own call on a short input (stopped after 60 seconds, where the original answers at once), and the ninth (line 79, `i + 3` to `i - 3`) was still in `_scan`'s loop, under test_magic_lines_inside_strings_and_brackets_are_left_alone, after 300 seconds. So all nine really hang, and the score stands.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `magics.py` | test_notebook_magics, test_notebook_magics_mutation, test_notebook_magics_windows, test_opportunities_analyzers_mutation, test_notebook_magics_boundary, test_notebook_formats_edges, test_notebook_formats, test_databricks_magics, test_notebook_practices, test_code_format (+ test_notebook_magics_uncapped) | 240 of 240 (232 + 8), uncapped | 222 + 9 / 9 | **96.3%** | 225/232 → 228/232 | 6/8 → 6/8 | 225 + 9 / 6 | **97.5%** | 6 equivalent |

All 6 left are the equivalents batches 33 and 164 explained, on the same lines.

### Bugs found

None.

### What the survivors were, and the tests that now kill them

- **magics.py**: an escaped backslash just before a triple quote's end still closes it; an empty triple-quoted string (`""""""`) opens and closes on its line, so a magic on the next line is still found; an empty block comment (`/**/`) between SQL statements ends where it closes. → `test_notebook_magics_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `magics.py` | 189 (two) | the bracket depth's step, `token == '('` negated or `==` to `!=` | Equivalent, as batch 33 found: only `depth == 0` is compared, and flipping every step's sign keeps that the same. |
| `magics.py` | 213 (two) | `i < 0` to `i < 1` and to `i <= 0` after looking for the end of a `--` comment | Equivalent, as batches 33 and 164 found: the search starts at the comment's own `-`, so it finds a later newline or none (-1), never 0. |
| `magics.py` | 218 (two) | `end < 0` to `end < 1` and to `end <= 0` after looking for `*/` | Equivalent, as batches 33 and 164 found: the search starts two characters in, so it finds 2 or more, or -1, never 0. |
