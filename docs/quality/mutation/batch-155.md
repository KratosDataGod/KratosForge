# Mutation testing, batch 155

A boundary re-score of two modules first scored in batches 3 and 6, before `mutate.py` made boundary mutants (batch 36). Their earlier scores count no `<` to `<=` or `>` to `>=` mutant, so each module was run again with `--kinds boundary` only:
- T-SQL on this PC: time travel's limits, decimal results at 38 digits, LIKE classes, and statements that end right after a word a check looks past (`localdata/tsql.py`, batch 3).
- The central policy and its promotion gate (`central/policy.py`, batch 6).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran. Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. For `localdata/tsql.py` the run used batch 3's test files; three tests in `test_tsql_loud_gaps.py` read `docs/`, so they were left out with `--deselect`. The other T-SQL test files (about 1,700 tests in all) were not in the run, since each mutant would have taken about a minute; a mutant they kill may show as a survivor here. For `central/policy.py`, `test_policy_promote_gate.py` was left out to keep each run short (about 30 s on its own); every mutant was killed without it.

The new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, on DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql.py` | test_local_tsql, test_local_tsql_more_edges, test_sql_lambdas, test_tsql_loud_gaps (+ test_local_tsql_boundary) | 55 of 55 | 17 / 38 | **30.9%** | 45 / 10 | **81.8%** | 10 equivalent |
| `central/policy.py` | test_policy_package, test_central_roles, test_governance, test_approvals_guard, test_central_upgrades | 22 of 22 | 22 / 0 | **100%** | 22 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/tsql.py**:
  - One FOR TIMESTAMP AS OF hint is taken, and a hint cut off right after its time is refused by name.
  - Time travel to exactly now is read. Exactly 120 days back is read with a note, and a second more is refused. Exactly 30 days back has no note, and a second more has one.
  - A product whose scales add up to exactly 38 is exact when it's small. A sum that keeps its scale is exact at 36 whole digits.
  - A decimal written with only a precision has scale 0. A whole number of exactly 2^31 is a decimal, and one less is an int.
  - A TIME(0) shown as text has no point.
  - LIKE classes: a lone `^` is the character, a `-` at the end of a class is the character, `[a-a]` is a range, and an escape at the very end of a class or a pattern is read without an error.
  - CHOOSE with one choice. TIMEFROMPARTS at precision 7, and fractions of exactly 10^precision refused. STR's length of 1 and 8000, and decimals of 0 and 16. ISJSON with one argument and with two.
  - SET STATISTICS, SET of an unknown option, `SET n +`, a lone CREATE, FOR JSON, COPY INTO ... FROM, and `copy` as an alias, each ending right after the checked word, are refused by name with no index error. CREATE SCHEMA named `[Authorization]` runs.
  - → `test_local_tsql_boundary.py`.
- **central/policy.py**: none.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/tsql.py` | 1167 | `precision > 38` to `>=` for `+` and `-` | Equivalent: at exactly 38, `38 - integral` is `scale + 1`, so the scale stays as it is. |
| `localdata/tsql.py` | 1175 | `precision > 38` to `>=` for `*` and `/` | Equivalent: at exactly 38 the scale is kept when the whole part is under 32, and when it is 32 or more the scale is already 6 or less. |
| `localdata/tsql.py` | 1177 | `integral < 32` to `<=` | Equivalent: at 32, `min(scale, 6)` and "6 when the scale is over 6" give the same scale. |
| `localdata/tsql.py` | 1179 | `scale > 6` to `>=` | Equivalent: a scale of 6 is set to 6. |
| `localdata/tsql.py` | 1288 | `abs(shift) > 38` to `>=` for a division | Equivalent: at a shift of exactly 38, the exact path's fit check holds only for a dividend of 0, so the answer comes from floating point either way. |
| `localdata/tsql.py` | 1298 | `p + moved > 38` to `>=` | Equivalent: at exactly 38 the added fit check always holds, since a value of `p` digits is below 10^p. |
| `localdata/tsql.py` | 2358 | `int(name) >= low` to `>` in `_whole` | Equivalent: a literal equal to the low end is then wrapped in a GREATEST or a CASE that gives the same answer. SUBSTRING, LEFT, RIGHT and REPLICATE at 0 and 1 were checked. |
| `localdata/tsql.py` | 2465 | `int(value) >= 0` to `>` for REPLICATE's count | Equivalent: a count that is 0 toward zero (0.5) takes the general path, which truncates it to 0 too. |
| `localdata/tsql.py` | 2479 | `value <= 1` to `<` for CHARINDEX's start | Equivalent: a start of 1 kept as 1 searches from the start, as dropping it does. |
| `localdata/tsql.py` | 904 | `len(tables) > 1` to `>=` for a DELETE | Equivalent as far as found: a DELETE naming its one target gives the same rows and count when it isn't typed as a SELECT, trailing-space comparisons included. |


