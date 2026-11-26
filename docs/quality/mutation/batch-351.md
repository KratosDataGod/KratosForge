# Mutation testing, batch 351

One module not scored before, run with `--kinds all`:
- Snowflake's SQL API from this PC (submit, follow and cancel a statement) and the connection check, field by field, without resuming a warehouse (`engines/snowflake_remote.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 157 mutants, so 150 were sampled; the sample held no boundary mutant. `main` has changed this module since this run; the new tests pass against today's version too.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_remote.py` | test_snowflake_remote, test_snowflake_auth (+ test_engines_snowflake_remote_mutation) | 150 of 157 (150 + 0) | 124 / 26 | **82.7%** | 124/150 → 147/150 | none sampled | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_remote.py**:
  - A statement asks Snowflake for 30 seconds, and each request waits 60.
  - A submit that gets a 5xx isn't sent again (it may have started); a look and a cancel that get one are tried again.
  - A cancel still goes out, and is tried again after a 429, when the person has pressed Cancel.
  - A cancel of a statement that already finished (422) is fine, any other refusal raises, and a handle that isn't one is refused before any request.
  - A network failure becomes "Couldn't reach ... (ConnectionError)".
  - An error made here has no status, code or SQL state.
  - Each refusal's hint (401, 403, 404, 408, 429) belongs to its own status.
  - Snowflake's error code is cut to 12 characters and its SQL state to 8.
  - The check keeps the account it was given, says "Signed in as" the user Snowflake named, names a schema it found, and reports a missing warehouse as a problem.
  - Roles that aren't JSON are no roles, and the role check fails cleanly instead of crashing.
  - → `test_engines_snowflake_remote_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/snowflake_remote.py` | 116 | Snowflake's message cut `[:300]` to `[:301]` | Equivalent: `redact.one_line` has already cut it to 300 characters. |
| `engines/snowflake_remote.py` | 171 | `zip(..., strict=True)` to `False` | Equivalent: the line before refuses any row whose length isn't the number of columns. |
| `engines/snowflake_remote.py` | 232 | `role_ok = True` to `False` | Equivalent: it is read only together with `role`, and when a role is set it is assigned again first. |
