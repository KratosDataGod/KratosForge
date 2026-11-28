# Mutation testing, batch 386

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Sign-in for a Snowflake connection's SQL API calls: key-pair tokens signed on this PC, programmatic access tokens from their references, and when a token is made again (`engines/snowflake_auth.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 73 mutants, and the run ran all 73 of 73 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_snowflake_auth, test_snowflake_auth_cov20, test_snowflake_remote, test_snowflake_remote_cov20 and test_snowflake_sql_engine, about 5 seconds together. Every key is made by the test that uses it, and the one made-up token is marked for the secret scan; no test reaches Snowflake.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (3, 1 and 1).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_auth.py` | test_snowflake_auth, test_snowflake_auth_cov20, test_snowflake_remote, test_snowflake_remote_cov20, test_snowflake_sql_engine (+ test_snowflake_auth_mutation) | 73 of 73 (71 + 2), uncapped | 67 / 6 | **91.8%** | 67/71 → 71/71 | 0/2 → 2/2 | 73 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_auth.py**:
  - A key-pair token lives 59 minutes (3540 seconds).
  - A token is made again from 120 seconds before it lapses: at 880 of 1000 it is made again, at 879.999 it is not.
  - A key file of exactly 64 KB is read; one byte more is refused as too large to be a key.
  - → `test_snowflake_auth_mutation.py`.

### Left

None.
