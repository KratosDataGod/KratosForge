# Mutation testing, batch 224

One module, run uncapped: every one of its mutants, not a sample of 150.
- The T-SQL best-practice checks (`sqlcompat/practices.py`).

Batch 23 sampled 150 of this module's mutants then, 547 in all (72.7% before, 92.0% after), and batch 39 ran its 40 boundary mutants (37.5% before, 75.0% after). The module has 596 mutants now. This run made all of them: `--cap 100000 --seed 7 --kinds all`, so 596 of 596, the boundary mutants (`<` to `<=`, `>` to `>=`) counted with the earlier kinds.

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The tests first passed once on the unmutated copy. Each pytest run had a temporary folder of its own, made fresh by a local plugin and removed when the run ended, so another run's leftover files could not fail a mutant and no run left files behind. Three "killed" mutants picked at random (44, 504 and 116) were run again by hand, and each failed a real assertion.

Batch 23 ran test_sql_practices, test_learn_fixes and test_learn_guides. The run here adds batch 23's own test_sqlcompat_practices_mutation and test_boundary_mutation_3 (its practices part); the score counts only the files given. The set runs in about 5 seconds, so no file was left out for time. Three tests read `docs/`, so they were left out: test_docs_practice_examples and test_practice_docs (whole files), and `test_learn_guides.py::test_the_shipped_guides_are_the_docs` with `--deselect`, as in batch 23.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. No mutant timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sqlcompat/practices.py` | test_sql_practices, test_learn_fixes, test_learn_guides, test_sqlcompat_practices_mutation, test_boundary_mutation_3 (+ test_sqlcompat_practices_uncapped) | 596 of 596 (555 + 41), uncapped | 510 / 86 | **85.6%** | 480/555 → 508/555 | 30/41 → 30/41 | 538 / 58 | **90.3%** | 53 equivalent, 3 not reached, 2 not killed |

The after score is short of 95%: 53 of the 58 left are equivalent, and 3 more are code no statement reaches. Most are the kinds batches 23 and 39 found: a helper's `return False` to `return None` that is only read as a condition (18 of them), a scan that starts one token earlier or later on a token that can't match, and the boundary mutants batch 39 explained, on lines moved by two. Without the 56 equivalent or not reached, the score would be 538 of 540.

### Bugs found

None.

### What the survivors were, and the tests that now kill them

- **sqlcompat/practices.py**:
  - Each rule's docs link; a query that isn't text is refused.
  - CREATE TABLE ... AS SELECT * and its ORDER BY are not a view or procedure; a JOIN at the end, and the first of two JOINs with one ON, are cross joins; only a MAX length column is reported; only ROW_NUMBER renumbers a key, reported at ROW_NUMBER; INSERT ... SELECT TOP (n) * is reported.
  - A SELECT * after INSERT ... VALUES in one BEGIN block doesn't feed the insert; UPDATE alias SET with a subquery, then FROM, reads the alias; SELECT ... INTO after a subquery in the select list is reported.
  - NOT IN a subquery that selects an expression is reported even when a column is filtered for NULLs; a subquery inside a function in WHERE, and a bracketed SELECT on its own, are not in a select list.
  - A wrapped column in WHERE after CASE ... END is reported, and one not followed by a comparison isn't.
  - Statements that end early (an open subquery, an open column list, NOT IN (SELECT) are still read.
  - A secret in a statement points at its line start and statement, and one outside every statement names only its line; a full reload names the TRUNCATE statement.
  - → `test_sqlcompat_practices_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sqlcompat/practices.py` | 398 (two) | `_correlated`'s check of the token before a name, `==` to `!=` and `j - 1` to `j - 0` | Not killed: each changes only the middle part of a three-part name in a subquery (`s.y.c`), which the original skips and the mutants read as a reference to `y`. Neither is clearly right, so no test pins it. |
| `sqlcompat/practices.py` | 139 | the secret line's out-of-range fallback, `0` to `1` | Not reached: `starts` has one entry for each line plus one, so a hit's line is always inside it. |
| `sqlcompat/practices.py` | 319 | `_select_into`'s answer at a lower depth, `False` to `True` | Not reached: `_verbs` gives only a statement's leading verbs, at the statement's own depth, so no token after one is lower (`(SELECT 1)`, `IF (SELECT ...)` and `UNION (SELECT ...)` the same). |
| `sqlcompat/practices.py` | 190 | the @variable check's `i > 0` to `i > 1` | Not reached: no statement starts with a variable followed by FROM, JOIN, INTO, UPDATE, TABLE or VIEW. |
| `sqlcompat/practices.py` | 250, 254, 255, 262, 266, 267, 309, 310, 319, 323, 324, 334, 340, 371, 375, 376, 402, 418 | a helper's `return False` to `return None` | Equivalent, as batch 23 found: each is read only as a condition. |
| `sqlcompat/practices.py` | 354, 358 | `_clause_of`'s `return ""` to `return None` | Equivalent: the result is only compared with `"WHERE"`. |
| `sqlcompat/practices.py` | 348 (two), 368 (two), 383 (two) | `_clause_of`, `_in_select_list` and `_correlated` scanning from one or two tokens earlier or later | Equivalent: the extra tokens are LIKE and its `'%...'` string, the bracket itself, or the token before it (SELECT or a comma), and none of them is a clause word, a stop word, FROM, JOIN or a name followed by a dot. |
| `sqlcompat/practices.py` | 278 (two), 419 (two), 421 (two) | `_close` from the call's name, the token before it, or OVER, instead of the bracket | Equivalent: the brackets of a call, of ROW_NUMBER() and of OVER (...) are at the call's own depth, so the first `)` at that depth after the earlier token is still the call's own. |
| `sqlcompat/practices.py` | 285 (three), 399 (three) | `k + 1 < close` to `k - 1 <`, `k + 0 <` and `<=`; `j + 3 >= close` to `j - 3 >=`, `j + 4 >=` and `>` | Equivalent: the index moved onto is at most `close`, where the token is `)`, neither `(` nor a dot. |
| `sqlcompat/practices.py` | 389 (two), 398 (two) | the table names from `j - 1` or `j`; `j == paren + 1` to `paren - 1` or `paren + 0` | Equivalent: FROM, JOIN, the token before them and SELECT are never a name followed by a dot. |
| `sqlcompat/practices.py` | 139 | the secret line check's `- 1` to `- 0` | Equivalent: a hit's line is at most the number of lines, and `starts` has one more entry. |
| `sqlcompat/practices.py` | 273 | the WHERE clause's end searched from WHERE itself | Equivalent: WHERE is not a clause end. |
| `sqlcompat/practices.py` | 139, 140, 190, 219, 276 (two), 333, 392, 394 | boundary mutants | Equivalent, for batch 39's reasons on lines moved by two: a hit's line is never past the last line, and a statement's end is trimmed; at 0, `toks[-1]` can't end where the first token starts; token `i` is DISTINCT, not GROUP; WHERE and the clause end aren't wrapping functions; at `close` the token is `)` or the last one, never FROM, AS or an alias. |
