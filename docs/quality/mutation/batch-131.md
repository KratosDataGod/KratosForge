# Mutation testing, batch 131

One engine module not scored before, run with `--kinds all`:
- Access tokens for an Azure Databricks connection: CLI profiles, the Databricks CLI's own token, OAuth machine-to-machine, secret references, Windows Credential Manager, the refresh before expiry and the memory of refused tokens (`engines/databricks_auth.py`).

The run was at `main` 5865db95 (2026-11-18), with the method of batch 130. The module has 190 mutants, so 150 were sampled. No `-k` filter or `--deselect` was needed.

The first run used test_databricks_auth_errors, test_databricks_auth_replies, test_databricks_credman, test_databricks_host, test_databricks_profile_host and test_credential_expiry. Each survivor was run again with test_databricks_sql, test_credman_dpapi_calls, test_databricks_discovery, test_databricks_jobs, test_dbconnect_env and test_engines_service_edges added, and the "before" figures are from that run. No mutant's run timed out. The new tests passed in a copy of `sidecar/` on its own, against `ast.unparse` of the module, and on DuckDB 1.5.6 and 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/databricks_auth.py` | test_databricks_auth_errors, test_databricks_auth_replies, test_databricks_credman, test_databricks_host, test_databricks_profile_host, test_credential_expiry, test_databricks_sql, test_credman_dpapi_calls, test_databricks_discovery, test_databricks_jobs, test_dbconnect_env, test_engines_service_edges (+ test_databricks_auth_mutation) | 150 of 190 (147 + 3) | 136 / 14 | **90.7%** | 134/147 → 147/147 | 2/3 → 3/3 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **engines/databricks_auth.py**:
  - The limits are the documented ones: a minute for the CLI, a refresh 120 s before expiry, 256 refused tokens remembered, Windows' 2,560-byte credential limit and 300 characters of the CLI's reason.
  - A token is fetched again from exactly 120 s before it expires, and not a moment earlier.
  - The refused-token memory keeps exactly the newest 256 and forgets the oldest first.
  - The CLI is started with its output captured as UTF-8 text, a 60 s timeout and no stdin.
  - advapi32 is opened with its last error kept.
  - A long reason from the CLI is cut to 299 characters and an ellipsis; one of exactly 300 is kept whole.
  - The token endpoint is asked with a 30 s timeout, and a lifetime under one second is still a lifetime.
  - Without an HTTP session given, the app's own session is used.
  - A CLI profile with a client ID but no secret, or a secret but no client ID, signs in with the CLI.
  - → `test_databricks_auth_mutation.py`, which also pins, for the first run's survivors that the wider run killed, an `env:` reference read from the given environment or this PC's, a missing CLI profile, a missing or hung CLI, a refused service principal, a connection holding a secret value, and the providers for a profile's service principal and for OAuth machine-to-machine.

### Left

None.

