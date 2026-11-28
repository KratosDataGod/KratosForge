# Mutation testing, batch 387

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Snowflake's SQL API from this PC: submitting, following and cancelling a statement, its result partitions, and the connection check that never resumes a warehouse (`engines/snowflake_remote.py`).

The run was at `main` 293fb38da (2026-11-24) and the "after" run at `main` 9c7201ae4, where the module and every file in the set are the same, with `sidecar/tools/mutate.py` as it is since #2902. The module has 176 mutants, and the run ran all 176 of 176 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_snowflake_remote, test_snowflake_remote_cov20, test_snowflake_sql_engine, test_snowflake_auth and test_snowflake_auth_cov20, about 3 seconds together.

A first run was ended part way by a restart of the machine, and was run again from the start; only the full run is scored. While that first run went, the machine's network proxy refused connections to a Fabric host and a Snowflake account host. Other test runs shared the machine, so the run again was made with a check, used only for this run and not committed, that refuses every connection from a test to anything but this machine and writes it down. The run and the "after" run wrote down none, and neither did the set on the module as it is. Every test here answers from a fake; none reaches Snowflake.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (7, 4 and 7).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/snowflake_remote.py` | test_snowflake_remote, test_snowflake_remote_cov20, test_snowflake_sql_engine, test_snowflake_auth, test_snowflake_auth_cov20 (+ test_snowflake_remote_mutation) | 176 of 176 (175 + 1), uncapped | 149 / 27 | **84.7%** | 149/175 → 172/175 | 0/1 → 1/1 | 173 / 3 | **98.3%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/snowflake_remote.py**:
  - A statement gives Snowflake 30 seconds, each request waits 60, and a statement isn't asynchronous unless asked.
  - A submit that gets a 503 is never sent again; a look at a running statement, a partition and a cancel are. A cancel is sent again even once Cancel was pressed.
  - A partition with a bad handle, or refused, raises.
  - A statement still running is cancelled and says how long it ran: "119 seconds", "61 seconds", and from 120 on in minutes ("2 minutes").
  - An error has no status, code or SQL state unless given. A refusal keeps 12 characters of the code and 8 of the state.
  - The hints for 408 and 429, and none for 409 or 430.
  - The connection check names the account and who signed in, says a found schema is visible, and fails a missing warehouse.
  - → `test_snowflake_remote_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `engines/snowflake_remote.py` | 256 | `role_ok = True` to False | Equivalent: `role_ok` is read only when the profile names a role, and then it is set again first. |
| `engines/snowflake_remote.py` | 122 | the message cut `[:300]` to `[:301]` | Equivalent: `redact.one_line` just before it already cuts the message to 300 characters. |
| `engines/snowflake_remote.py` | 195 | `zip(..., strict=True)` to False | Equivalent: the lines above refuse any row whose length isn't the number of columns, so the lengths always match. |
