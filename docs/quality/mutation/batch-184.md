# Mutation testing, batch 184

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Connection profiles: which platforms a piece of work uses, its sign-in references, export and import, switching, and reading the Databricks and Snowflake CLIs' own files (`connprofiles.py`).

Batch 13 sampled 150 of this module's 406 mutants then (91.3% before, 100% after). It now has 411, and this run ran all 411 of 411.

The run was at `main` ad1ac4cf8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 and the method of batch 182: tests first passed on the unmutated copy, paths relative to `sidecar/`, and the lane's own `--basetemp` (batch 182 says why). The set is batch 13's set with the two `*_mutation` files that call the module. The other files that call it (test_databricks_billing, test_databricks_bundles_service, test_databricks_runs_service, test_databricks_sync_service, test_dbconnect, test_dbconnect_env, test_engine_conformance, test_platforms) were left out to keep each mutant's run short: batch 13 ran them against its survivors, and they killed none. No `-k` filter or `--deselect` was needed. Three kills picked at random were checked by hand, as in batch 182, and each failed real tests (6, 13 and 44).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They passed both ways.

Three mutants timed out, and they count as killed. All three are in `import_`'s loop for a new id (line 457): with the test negated, flipped or made `or`, a profile id that is free makes the loop draw ids forever.

**Rechecked on 2026-11-24.** On a machine this loaded a mutant can time out without hanging, so all three were applied again, alone, to `sidecar/` as it was at the batch's commit and the set run with a 1,500-second limit instead of 120. All three really never end, and the numbers don't change: each was stopped by the test suite's own 300-second limit for one test, with the stack in `import_`'s loop for a new id.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `connprofiles.py` | test_connprofiles, test_connprofiles_edges, test_connprofiles_key_text, test_connprofiles_shape, test_connprofiles_mutation, test_databricks_host, test_databricks_profile_host, test_engine_connections_settings, test_engines_service, test_engines_service_edges, test_engines_service_mutation, test_secret_detectors_userinfo (+ test_connprofiles_uncapped) | 411 of 411 (406 + 5), uncapped | 395 / 16 | **96.1%** | 391/406 → 406/406 | 4/5 → 5/5 | 411 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **connprofiles.py**:
  - A key file that isn't a full path says so, with the Windows example only on Windows.
  - A key in a message is kept up to 60 characters and cut to 59 and "…" after that.
  - `normalize` gives a 14-character `p-` id, `prefer_local` on and serverless off.
  - A budget of 0 minutes is allowed; -1 isn't.
  - A Databricks config without a section header is found, with no profiles and an error that names only the exception type. A profile with a client id but no client secret signs in with the CLI, not as oauth-m2m.
  - A CLI profile on AWS keeps that cloud.
  - Snowflake connections are read from `config.toml` when there is no `connections.toml`, and a `connections` entry there that isn't a table is no connections. Only group or other write bits earn the chmod note (POSIX only).
  - Import names each connection to set up by its id, not its platform.
  - Switching with one open session says "session", not "sessions".
  - → `test_connprofiles_uncapped.py`.

