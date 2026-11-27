# Mutation testing, batch 350

One module not scored before, run with `--kinds all`:
- Signing in to Snowflake's SQL API: a key-pair token made on this PC or a programmatic access token from its reference, made again shortly before it lapses, and never sent again once refused (`engines/snowflake_auth.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 73 mutants, so all were run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_auth.py` | test_snowflake_auth, test_snowflake_remote (+ test_engines_snowflake_auth_mutation) | 73 of 73 (71 + 2) | 66 / 7 | **90.4%** | 66/71 → 71/71 | 0/2 → 2/2 | 73 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_auth.py**:
  - A key-pair token lives 59 minutes. The tests compared against the module's own constant, so a changed lifetime passed.
  - A token is made again from exactly two minutes before it lapses, and kept at two minutes and one second.
  - A fetch that gives no token is refused ("No Snowflake credential was produced") rather than sent.
  - A key file of exactly 64 KiB is read (and then refused as not a key), and one byte more is refused as too large.
  - → `test_engines_snowflake_auth_mutation.py`.

### Left

None.
