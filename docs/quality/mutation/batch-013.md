# Mutation testing, batch 13

Three modules no open pull request is changing:
- Connection profiles: which platforms a piece of work uses, its sign-in references, export and import, and reading the Databricks and Snowflake CLIs' own files (`connprofiles.py`).
- The result viewer behind a notebook table output: paging, the profile, charts and downloads, with its small Excel writer (`frames.py`).
- The Databricks SQL warehouse engine (`engines/databricks_sql.py`).

The run was at `main` fb729823b (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 12 (sample cap 150, seed 7), on DuckDB 1.5.6. All three modules have more points than the cap, so 150 of each were run.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test passed both ways, so no run below can score a false 100%. One extra test was left out of the `connprofiles.py` re-run because it reads a page under `docs/`: `test_platforms.py::test_the_docs_table_is_the_codes`.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `connprofiles.py` | test_connprofiles, test_connprofiles_edges, test_connprofiles_key_text, test_connprofiles_shape, test_databricks_host, test_databricks_profile_host, test_engine_connections_settings, test_engines_service, test_engines_service_edges, test_secret_detectors_userinfo (+ test_connprofiles_mutation) | 150 of 406 | 137 / 13 | **91.3%** | 150 / 0 | **100%** | none |
| `frames.py` | test_frames, test_frames_more_edges, test_notebook_display_edges, test_outputs_hardening, test_query_profile, test_spark_summary_schema, test_wrangler, test_write_types (+ test_frames_mutation) | 150 of 424 | 126 / 24 | **84.0%** | 149 / 1 | **99.3%** | 1 with no deterministic test |
| `engines/databricks_sql.py` | test_credential_expiry, test_databricks_discovery, test_databricks_sql, test_databricks_sql_errors, test_databricks_sql_robust, test_paging, test_remote_text_lines, test_reply_text_one_line (+ test_databricks_sql_mutation) | 150 of 373 | 133 / 17 | **88.7%** | 150 / 0 | **100%** | none |

The 7 timeouts (2 in `connprofiles.py`, 1 in `frames.py`, 4 in `engines/databricks_sql.py`) are real hangs: each mutant turns a loop's exit or a lock's non-blocking try into one that never ends (`import_`'s new-id loop, `_unique`'s suffix loop, the statement poll, and a second cell waiting on the first).

Existing test files the first sets left out were tried on the survivors first:
- `connprofiles.py`: test_databricks_billing, test_databricks_bundles_service, test_databricks_runs_service, test_databricks_sync_service, test_dbconnect, test_dbconnect_env, test_engine_conformance, test_platforms. They killed none.
- `frames.py`: test_kernel_process_edges, test_notebook_history, test_pandas_in_notebooks, test_publish_mutation, test_service_edges, test_state_writes. They killed none.
- `engines/databricks_sql.py`: test_cli_tenant_commands, test_databricks_billing, test_databricks_jobs, test_databricks_runs_service, test_databricks_sync_service, test_databricks_workspace_sync, test_databricks_workspace_sync_edges, test_dbconnect_env, test_engine_conformance, test_engines_service, test_engines_service_edges. They killed 2: the adapter's `describe()` and a whole result asked for with `result_rows` of 1.

The new tests killed the rest. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **connprofiles.py**:
  - A private key file on a network share (`\\server\...` or `//server/...`) refused with its own reason.
  - Import refusing a secret anywhere in the document, not only inside its profile.
  - Databricks on AWS or GCP refused, since only Azure is turned on.
  - A service principal needing both its client id and its secret reference.
  - The local cache defaulting to 10 GB.
  - Export marking only filled-in credential fields as needing setup.
  - The Databricks CLI file reading as found, and a profile with only a host signing in through the CLI.
  - A CLI profile or connection name making an id of 40 characters.
  - The Snowflake CLI folder following this computer's system.
  - A Snowflake CLI connection warned about only when it uses a sign-in KratosForge can't.
  - Deleting a profile that was never saved.
  - → `test_connprofiles_mutation.py`.
- **frames.py**:
  - A page of 100 rows by default, and frame ids of 12 hex characters.
  - The Arrow table made once and reused.
  - An Excel download of 1,048,576 rows refused before anything is written.
  - The profile covering 200 columns and saying how many more there are, sampling only above 250,000 rows, and drawing 12 histogram bins.
  - The type names for true/false and struct columns, and a NaN chart category keeping its own key.
  - A bar chart keeping the largest categories first, and a line chart keeping x order with a missing x last.
  - Summing a decimal column keeping its fractions, and count counting rows where the value is missing or no value is picked.
  - A series chart with no "Other" slot, and a histogram binning by the values' range.
  - Excel dates around the 1900 leap day that never was, decimals of more than 15 digits going out as text, column letters past Z, text keeping tabs and line breaks, and each row getting its own number.
  - → `test_frames_mutation.py`.
- **engines/databricks_sql.py**:
  - The two-hour limit, counted from when the statement was sent, and its message in minutes.
  - An error cell reporting its own running time.
  - A plain cell and Plan sending no row limit, and a whole result of one row asking for two.
  - A whole result's preview starting at its first row, and a first chunk that says it starts at row 0 being read.
  - A whole result of exactly the size limit kept.
  - A result chunk that isn't an object refused by name.
  - A refused token tried once more with a new one, a new token that is refused too being forgotten, and a sign-in that can't be renewed being a 401 error.
  - Requests waiting a minute at most.
  - Cancel tried again after Cancel was pressed, while other calls aren't.
  - A cell's catalog and schema winning over the connection's.
  - → `test_databricks_sql_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `frames.py` | 457 | `sname in values and _chart_key(k) in index` → `or` | Every series name the chart's last query gives is in `values`, and its x values come from the same ordered list of kept categories. They can only differ when categories tie at the 60-category cut and DuckDB orders the tie differently in two queries, which no test can make happen on demand. |

### Real bugs found

None.

### Not in this batch

- `connprofiles.py`, `frames.py` and `engines/databricks_sql.py` past the 150 sampled mutants.

