# Mutation testing, batch 213

One module, run uncapped: every mutant, not a sample. Batch 28 ran all of its then 145 point mutants (87.6% before, 97.9% after), and batch 169 its 2 boundary mutants (50.0%, one equivalent).
- Whether SQL written for one platform runs on another (`sql_portability.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 147 of 147 mutants ran (145 point + 2 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is batches 28 and 169's files, which are every test file that imports the module. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sql_portability.py` | test_sql_portability, test_sql_portability_mutation, test_kudp_guardrails, test_snowflake_sql_variadic | 147 of 147 (145 + 2) | 143 / 4 | **97.3%** | 142/145 → no change needed | 1/2 → no change needed | 143 / 4 | **97.3%** | 4 equivalent |

No bugs were found, and no test was added: every survivor is one of the equivalents batches 28 and 169 found, at the same place in the code.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sql_portability.py` | 137 (2) | no QUALIFY in `_alias_clash`: `return False` dropped, or → `True` | Equivalent: it's only called for a SELECT that has QUALIFY. |
| `sql_portability.py` | 179 | `return` dropped for a rewrite that isn't a SELECT | Equivalent: sqlglot's `eliminate_qualify` gives a SELECT back for a SELECT. |
| `sql_portability.py` | 244 | `_RANK[found] > _RANK[grade]` to `>=` | Equivalent: at equal rank `found` is `grade`, so the grade doesn't change. |
