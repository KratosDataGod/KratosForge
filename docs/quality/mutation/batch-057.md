# Mutation testing, batch 57

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- `%autoreload` and `%aimport` on this PC: the modes, which modules are looked at, and reloading a changed one (`autoreload.py`).
- Profile: column statistics and the clean and check rules suggested for a table (`profiling.py`).
- Try the fix beside a SQL best-practice finding: where a finding points, the rewrite and the reads Compare runs (`learn/fixes.py`).

The run was at `main` d37fd8ce8 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. `profiling.py` has 196 mutation points and `learn/fixes.py` 261, so 150 of each were sampled. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `autoreload.py` | test_autoreload_edges, test_notebook_autoreload (+ test_autoreload_mutation) | 70 of 70 (69 + 1) | 54 / 16 | **77.1%** | 54/69 → 67/69 | 0/1 → 1/1 | 68 / 2 | **97.1%** | 2 equivalent |
| `profiling.py` | test_profiling_edges, test_cloud (+ test_profiling_mutation) | 150 of 196 (138 + 12) | 88 / 62 | **58.7%** | 86/138 → 136/138 | 2/12 → 11/12 | 147 / 3 | **98.0%** | 3 equivalent |
| `learn/fixes.py` | test_learn_fixes, test_learn_fixes_edges (+ test_learn_fixes_mutation) | 150 of 261 (138 + 12) | 120 / 30 | **80.0%** | 116/138 → 130/138 | 4/12 → 8/12 | 138 / 12 | **92.0%** | 12 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **autoreload.py**:
  - Each mode word (`0`, `off`, `1`, `explicit`, `2`, `all`, `3`, `complete`) sets its mode.
  - A new reloader is off, so even a changed module named with `%aimport` isn't reloaded until a mode is set.
  - `%aimport` remembers a module's time, and a module whose time hasn't moved since is not reloaded; one that changed is reloaded in mode 1.
  - Off answers with an empty list.
  - Skipped modules, `__main__` and empty `sys.modules` entries are never reloaded.
  - `%aimport` with no names lists both sets, sorted, with "none" for an empty one.
  - A reload that fails prints the frame where it failed, and the cell still runs.
  - → `test_autoreload_mutation.py`.
- **profiling.py**:
  - The TOML a suggestion carries writes true and false, numbers, inline tables, lists and quoted text.
  - The limits are 12 accepted values and 5 top values, and the top values are the most common with their counts.
  - An all-null column has no distinct values or top values; the null share is a percent to one place, and an empty table has none.
  - A clean suggestion has no `on_fail`, and a check warns by default.
  - A rule already in place isn't suggested again, and the issue says a clean rule handles it.
  - Case is lower when exactly half the values are lower case, and the note counts the extra spellings.
  - Accepted values need 2 to 12 values and 3 rows for each.
  - Far outliers lie beyond three spreads of the middle half, exactly at a fence is not one, and there are none when the middle half has no spread or the column is key-like.
  - A range needs 3 values, none below 0; a few negatives are noted up to 1 in 100.
  - Date and date-and-time columns show their first and last values.
  - A named key must not be null and must be unique, and stops the run; an `_id` column with no keys named gets warning checks; unique isn't suggested for one row, beside another named key, or for a name not ending `_id`.
  - Repeated values are noted only for a named key, and a few nulls up to 1 in 100.
  - → `test_profiling_mutation.py`.
- **learn/fixes.py**:
  - A finding on line 1, or in the first column of a later line, gets its fix; one on line 0 gets none.
  - `YEAR` at the end of a statement, without `= <year>` after it, or followed by one more operator is left alone; followed by `AND` it is fixed.
  - The reason names the column, not its qualifier, and a column named with three parts is left alone.
  - `FROM` as the last word isn't read as a table, a table at the very end of the statement is read, and two tables after one `FROM` are left alone even with aliases.
  - SQ005 puts `dbo.` before the name as written, brackets included, and says why.
  - Compare runs a view's query, isn't offered for CREATE TABLE ... AS SELECT or a view with nothing after `AS`, and leaves out a closing semicolon.
  - → `test_learn_fixes_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `autoreload.py` | 63, 101 | `force_mode=2` and `_candidates(2)` to 3 | Equivalent: `_candidates` only tells mode 1 apart, so modes 2 and 3 pick the same modules. |
| `profiling.py` | 68 | `return False` to `return None` | Equivalent: the answer is only read as true or false. |
| `profiling.py` | 105 | `folded` for no real values 0 to 1 | Equivalent: with no real values, `folded` is only read by `1 < folded`, false for 0 and 1. |
| `profiling.py` | 165 | `nulls < n` to `nulls <= n` | Equivalent: when every value is null the null share is 100%, so `null_pct <= 1` is already false. |
| `learn/fixes.py` | 89 | `return -1` to `return None` | Equivalent: no token starts at -1 or None, so the finding gets no fix either way. |
| `learn/fixes.py` | 88 | `col < 1` to `col < 0` | Equivalent: column 0 points at the line break before the line or before the query, where no token starts. |
| `learn/fixes.py` | 108 | `_close(s, i + 1)` to `_close(s, i)` | Equivalent: `YEAR` and its `(` have the same depth, so the same `)` is found. |
| `learn/fixes.py` | 189 | two moves in the bracketed-schema branch | Equivalent: SQ005 only fixes names in `dbo`, which needs no brackets, so the branch never runs. |
| `learn/fixes.py` | 203 | `len(toks) > 2` to `> 3` and to `>= 2` | Equivalent: `CREATE VIEW` or `CREATE VIEW v` has no `AS`, so Compare is not offered either way. |
| `learn/fixes.py` | 210 | `t.start >= start` to `>` | Equivalent: the token at `start` is `SELECT` or `WITH`, never a write. |
| `learn/fixes.py` | 213, 214 | the closing-semicolon check moved or negated | Equivalent: the statement splitter leaves the `;` out of a statement's tokens, so there is never one to trim. |
| `learn/fixes.py` | 215 | `start <= fix start` to `<`, and `fix start < fix end` to `<=` | Equivalent: a fix never starts on the `SELECT` or `WITH` the read starts at, and is never empty. |

