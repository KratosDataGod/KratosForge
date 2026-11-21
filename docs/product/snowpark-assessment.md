# Snowpark assessment (SF-007)

Research for backlog item SF-007 in [platform-parity-backlog.md](platform-parity-backlog.md). It asks
which Snowpark Python calls KratosForge could check on this PC. Nothing here is built, and no parity
cell changes. Building any of it needs Chris's typed go.

- **Researched.** 2026-11-21, against Snowpark Python 1.58.0, the latest release on
  [PyPI](https://pypi.org/project/snowflake-snowpark-python/) that day.
- **Method.** Snowflake's developer guide and API reference, the package metadata, and a read of the
  1.58.0 wheel's source. Nothing was installed into KratosForge or its test environment, and nothing
  was run against Snowflake.
- **Wording.** Every check described here is a compatibility check. None of it shows that code runs
  as it would on Snowflake.

## Summary

Snowflake ships an official local testing framework inside the Snowpark Python package. A session
created with `Session.builder.config('local_testing', True).create()` runs DataFrame operations in
memory with no Snowflake account
([local testing framework](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally)).
It is Apache 2.0 licensed
([PyPI](https://pypi.org/project/snowflake-snowpark-python/),
[GitHub](https://github.com/snowflakedb/snowpark-python)). It covers the DataFrame core, a subset of
functions, in-memory tables, CSV and JSON reads from mocked stages, scalar UDFs and stored procedures.
It refuses `session.sql`, UDTFs, vectorized UDFs, async jobs and several data types.

KratosForge already plans exactly this. The `snowpark-local` engine in
`sidecar/fabric_desktop/engines/registry.py` says it will use Snowflake's local testing framework in
its own runtime folder, and [engines.md](../engines.md#not-yet) says the same. This assessment
confirms that plan is the right base. Translating Snowpark to DuckDB would mean rebuilding what
Snowflake already ships.

Some calls can never be checked on this PC. Anything that needs Snowflake's servers belongs there.
That covers Anaconda package resolution, stage imports, external access, secrets, Cortex AI functions,
pandas on Snowflake, query history and lineage.

## What KratosForge has today

- **Snowflake SQL on this PC.** `sidecar/fabric_desktop/snowflake_sql.py` reads Snowflake SQL with
  sqlglot, rewrites it for DuckDB and labels each statement translated, difference or
  not_translatable. Another workstream owns it. This assessment only reads it.
- **PySpark on DuckDB, the closest analogue.** `spark_local.py` wires DuckDB's PySpark-compatible API
  to the lakehouse, and `spark_compat.py` adds the PySpark calls DuckDB lacks by writing each one as
  Spark SQL and translating it with sqlglot. `spark_api_coverage.py` runs every documented example of
  PySpark 3.5's DataFrame API on this PC and labels each method runs, differs, refused, fails or not
  checked, against outcomes captured by hand from real Spark (`spark_dataframe_api.json`). That is
  dozens of modules of work, because DuckDB's Spark API was only a start.
- **Locked runtime folders.** `sidecar/fabric_desktop/engines/runtimes.py` installs a platform's own
  client into a folder of its own from a hash-pinned lock file and runs each session as a separate
  process. Databricks Connect uses it. A Snowpark runtime would too.
- **Parity.** `parity.py` has the Snowflake Snowpark area with local execution Missing and plan
  `C / P2 / none / SF-007`. This assessment changes neither.

## The Snowpark Python surface

| Family | What it is | Source |
| --- | --- | --- |
| Session | `Session.builder`, `config`, `create`, `sql`, `table`, `create_dataframe`, `call`, `add_packages`, `add_import`, `file` | [Session](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/session) |
| DataFrame | `select`, `filter`/`where`, `with_column`, `join`, `group_by`, `agg`, `sort`, `limit`, `union`, `union_all`, `union_by_name`, `pivot`, `unpivot`, `flatten`, `collect`, `show`, `to_pandas`, `cache_result`, `write` and more | [DataFrame](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/dataframe) |
| Window | `Window.partition_by`, `order_by`, frame bounds | [Window](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/window) |
| Functions | About 462 in `snowflake.snowpark.functions`, including `ai_*` and `snowflake_cortex_*` | [Functions](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/functions) |
| Types | `snowflake.snowpark.types` | [Data Types](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/types) |
| Read and write | `DataFrameReader` (csv, json, parquet, avro, orc, xml, table, dbapi, jdbc), `DataFrameWriter` (`save_as_table`, `insert_into`, `copy_into_location`), `FileOperation` (`put`, `get`, `list`, `remove`, `copy_files`) | [Input/Output](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/io) |
| UDFs | Scalar UDFs run on the server, with packages from the Snowflake Anaconda channel and imports uploaded to a stage | [Creating UDFs](https://docs.snowflake.com/en/developer-guide/snowpark/python/creating-udfs), [UDF](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/udf) |
| UDTFs and UDAFs | Table functions and aggregate functions | [UDTF](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/udtf), [UDAF](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/udaf) |
| Stored procedures | `@sproc`, `register`, `session.call` | [Stored Procedures](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/stored_procedures) |
| pandas on Snowflake | The modin-based Snowpark pandas API | [pandas on Snowflake](https://docs.snowflake.com/en/developer-guide/snowpark/python/pandas-on-snowflake) |

## Snowflake's local testing framework

All of this section comes from the
[local testing framework](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally)
page unless another source is named.

- **Enabling it.** `Session.builder.config('local_testing', True).create()`. The API is the same as a
  live session, so one test can run either way.
- **Installing it.** `pip install "snowflake-snowpark-python[localtest]"`, version 1.18.0 or later.
  The package needs Python 3.10 to 3.14 ([PyPI](https://pypi.org/project/snowflake-snowpark-python/)),
  so KratosForge's Python 3.12 fits.
- **Supported.** Creating DataFrames from Python values, files and pandas, and running operations on
  them. `save_as_table` into in-memory temporary tables read back with `Session.table`. `session.file.put`
  and CSV or JSON reads from the mocked stage. `SnowflakeFile.open` on local and mocked stage files
  (1.35.0 or later). Scalar UDFs and stored procedures through `@udf`, `@sproc`, `register` and
  `register_from_file`, called with `call_udf` or `session.call`. A subset of functions, including
  window functions.
- **Extending it.** Built-in functions it lacks can be supplied with `@patch` from
  `snowflake.snowpark.mock`. `session.sql` can be mocked with `unittest.mock.patch.object` to return a
  prepared DataFrame.
- **Limitations Snowflake doesn't plan to address.** Raw SQL strings and anything that parses SQL,
  such as `session.sql`. Async operations. Objects outliving the session. Collation. `Variant`,
  `Array` and `Object` beyond standard JSON. Patching rank-related functions. SQL format models, so
  `to_decimal` ignores `format`. Stage create and drop. Package validation for UDFs and procedures,
  so every package must already be installed. Query tags, query history and lineage. UDF and
  procedure options `parallel`, `execute_as`, `statement_params`, `source_code_display`,
  `external_access_integrations`, `secrets` and `comment`, which are ignored. SYSTEM and BLOCK
  sampling, which behave like ROW sampling.
- **Not supported yet, raising `NotImplementedError`.** UDTFs and UDAFs. Vectorized UDFs and UDTFs.
  Built-in table functions. Table stored procedures. `Geometry`, `Geography` and `Vector`. Interval
  expressions. File formats other than CSV and JSON. Some read options, such as `infer_schema` for CSV.
- **Known issues.** Window functions work in `select` but not inside `group_by` or other
  aggregations. Duplicate column names come back as one column. `Table.merge` and `Table.update` need
  the nondeterministic-update errors off and update one row in a multi-join. Fully qualified stage
  names aren't supported by GET and PUT. `DataFrame.pivot` takes only static values. pandas
  DataFrames with time-zone-aware timestamps can't be loaded.

The 1.58.0 wheel's source adds the following. It was read by hand and is not a Snowflake statement.

- `snowflake/snowpark/mock/_functions.py` registers 86 function names, against about 462 public
  functions. Arithmetic, comparison, casts and `lag`, `lead`, `first_value` and `last_value` are
  handled in `mock/_plan.py` instead. Every other function raises until it is patched. The exact
  per-function picture is unconfirmed until a coverage run like `spark_api_coverage.py` measures it.
- `mock/_udtf.py` and `mock/_udaf.py` define mock registration classes, although the page lists
  UDTFs and UDAFs as unsupported. Which is current is unconfirmed. Treat them as unsupported.
- `mock/_analyzer.py` has a branch for `copy_into_location`. Whether it writes anything usable to the
  mocked stage is unconfirmed.
- `mock/_telemetry.py` sends out-of-band telemetry to `client-telemetry.snowflakecomputing.com` when a
  local session is created and when an unsupported call is hit, unless the session option
  `disable_local_testing_telemetry` is set. The local testing page doesn't mention this. An embedded
  runtime must set the option, and the worker should also have no network route for it. This is the
  main reason to read the code before trusting the label "local".
- The wheel's own requirements include `snowflake-connector-python` even for local testing
  ([PyPI](https://pypi.org/project/snowflake-snowpark-python/)). Connector 4.8.1 is Apache 2.0 and
  brings `cryptography`, `pyOpenSSL`, `pyjwt`, `boto3`, `botocore`, `requests` and others. The
  `localtest` extra adds `pandas` and `requests`.

pandas on Snowflake needs a live Snowflake session even for work that hybrid execution runs locally
([pandas on Snowflake](https://docs.snowflake.com/en/developer-guide/snowpark/python/pandas-on-snowflake)).
The local testing page doesn't mention it, so it is outside what local testing covers.

## Coverage

Three kinds of check are possible on this PC.

- **Static.** Read the cell's code without running it and check that each Snowpark call and keyword
  exists in a pinned Snowpark version's signatures. It says nothing about results.
- **Local run.** Run the cell in a Snowflake local testing session (option A), or translate its SQL
  through `snowflake_sql.py` to DuckDB (option B). Results are compatibility evidence only.
- **Not locally checkable.** It needs Snowflake's servers.

| Call family | Static check | Local run | Not locally checkable | Source |
| --- | --- | --- | --- | --- |
| `Session.builder`, `config`, `create` | Yes | Yes, with `local_testing` | Account, role and warehouse settings | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `session.sql` | Yes | Not in local testing. Through the translator (B), labelled per statement | Statements the translator refuses, such as streams, tasks and time travel | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `create_dataframe`, `table`, `collect`, `show`, `to_pandas` | Yes | Yes | Persistent tables from a real account | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `select`, `filter`, `with_column`, `sort`, `limit` | Yes | Yes. Per-method coverage unconfirmed | | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally), [DataFrame](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/dataframe) |
| `join` | Yes | Yes, but duplicate column names collapse to one | | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `group_by`, `agg` | Yes | Yes, but no window function inside an aggregation | | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `union`, `union_all`, `union_by_name` | Yes | Likely. Not named on the page, unconfirmed | | [DataFrame](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/dataframe) |
| `Window`, `row_number`, `rank`, `lag`, `lead` | Yes | Yes, in `select` | | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally), [Window](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/window) |
| `pivot`, `unpivot` | Yes | `pivot` with static values only. `unpivot` unconfirmed | | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `snowflake.snowpark.functions` | Yes | A subset. Others raise until patched | | [Functions](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/functions) |
| `ai_*`, `snowflake_cortex_*` | Yes | No | Yes, Cortex runs only on Snowflake | [Functions](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/functions) |
| `snowflake.snowpark.types` | Yes | Yes, except `Geometry`, `Geography`, `Vector` and intervals. Semi-structured as standard JSON | | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally), [Data Types](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/types) |
| `session.read.csv`, `json` after `file.put` | Yes | Yes, no `infer_schema` for CSV | | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `session.read.parquet`, `avro`, `orc`, `xml`, `dbapi`, `jdbc` | Yes | No | Yes | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally), [Input/Output](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/io) |
| `save_as_table`, `insert_into`, `Table.merge`, `Table.update` | Yes | Yes, in memory for the session, with the merge caveats | Anything that should persist | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `copy_into_location`, `copy_into_table`, `file.get`, `list`, `remove` | Yes | Unconfirmed, see the source notes above | Real stages, external stages, storage integrations | [Input/Output](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/io) |
| Scalar `@udf`, `register`, `call_udf` | Yes | Yes, in process, with packages already installed | Anaconda package resolution, `add_import` from stages, `external_access_integrations`, `secrets` | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally), [Creating UDFs](https://docs.snowflake.com/en/developer-guide/snowpark/python/creating-udfs), [External access](https://docs.snowflake.com/en/developer-guide/external-network-access/creating-using-external-network-access) |
| UDTFs, UDAFs, vectorized UDFs | Yes | No | Yes, until local testing supports them | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `@sproc`, `session.call` | Yes | Yes, except table procedures. `execute_as` ignored | Owner's rights and caller's rights | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| `collect_nowait`, `AsyncJob` | Yes | No | Yes | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| Query tags, query history, lineage | Yes | No | Yes | [Local testing](https://docs.snowflake.com/en/developer-guide/snowpark/python/testing-locally) |
| pandas on Snowflake (modin) | Yes | No | Yes, it needs a Snowflake session | [pandas on Snowflake](https://docs.snowflake.com/en/developer-guide/snowpark/python/pandas-on-snowflake) |

"Yes" in the local run column means Snowflake documents it as supported in local testing. It doesn't
mean the result matches Snowflake. Only a recorded live run could say that, and that is out of scope.

## Options

### A. Embed Snowflake's local testing mode

Run Snowpark cells in a local testing session inside a locked runtime folder, as the `snowpark-local`
engine already plans.

- **What it checks.** Everything in the local run column marked Yes, with Snowflake's own semantics
  for whatever it implements and `NotImplementedError` for the rest.
- **Cost.** Medium. A runtime folder and lock file like Databricks Connect's, a worker that opens the
  session with telemetry off, a refusal message for `NotImplementedError`, and a coverage file like
  `spark_api_coverage.py` built from Snowpark's documented examples so the docs say what runs.
- **Risks.** A large new dependency tree, mostly from the connector, that the app must pin, hash and
  update. Telemetry that is on by default (see above). Snowflake changes the mock between releases,
  so coverage must be re-measured on each upgrade. People may read a pass as Snowflake behaviour.
- **Wording.** "Compatibility check, simulated by Snowflake's local testing framework." Never "runs
  like Snowflake", "equivalent" or "verified".

### B. Translate to DuckDB through the existing translator

Implement a Snowpark-shaped API that builds Snowflake SQL and runs it through `snowflake_sql.py` on
DuckDB, as `spark_compat.py` does for PySpark.

- **What it checks.** Whatever both the new API layer and the translator cover. It is the only option
  that can run `session.sql`.
- **Cost.** Large. DuckDB has no Snowpark API to start from, unlike its PySpark one, so every
  DataFrame method, function and type would be written by KratosForge. It would also add load to a
  translator another workstream owns.
- **Risks.** Two translation layers, each with its own differences. KratosForge would own the
  semantics, not Snowflake. It is building where embedding is possible.
- **Wording.** "Compatibility check, translated to DuckDB." Each statement keeps the translator's
  translated, difference or not_translatable label.

A narrow form of B is worth keeping. Inside option A, `session.sql` could be routed to the
translator through the mocking hook Snowflake documents, with the translator's label on every result.
That is a later decision, not part of the base.

### C. Static API check only

Check a cell's Snowpark calls against signatures captured from a pinned Snowpark version, without
running anything.

- **What it checks.** That each call, method and keyword exists in that version, and flags calls in
  the not-locally-checkable families (Cortex, external access, stages, pandas on Snowflake) before
  anyone runs them on Snowflake.
- **Cost.** Small. A capture tool run by hand with the Snowpark wheel, like
  `sidecar/tools/capture_spark_dataframe_api.py`, writes a signature file that ships with the app. The app
  itself adds no dependency.
- **Risks.** Python can't always tell which object a call is on, so some calls stay unknown. It says
  nothing about results.
- **Wording.** "API compatibility check." Never "validated" or "runs".

## Recommendation

Embed Snowflake's local testing mode (A) as the base for running Snowpark on this PC, in its own
locked runtime folder with telemetry off. Start with the static check (C), because it needs no new
app dependency and gives the not-locally-checkable list straight away. Don't build B as a base. Decide
separately whether `session.sql` inside A goes through the existing translator.

The parity cell for Snowpark local execution stays Missing until something is built. Built on A, it
could reach Partial, never Verified, because verified equivalence needs a recorded live run.

## What this needs from Chris

1. **The typed go** before any runtime is built, as the backlog requires.
2. **A new dependency**, for option A only. `snowflake-snowpark-python[localtest]` pinned to one
   version, with `snowflake-connector-python` and its tree (`cryptography`, `pyOpenSSL`, `pyjwt`,
   `boto3`, `botocore`, `pandas`, `requests` and others), all Apache 2.0 at the top level, installed
   into a locked runtime folder and never into the sidecar. The licences of the transitive packages
   are not checked yet.
3. **Telemetry.** Agreement that the runtime always sets `disable_local_testing_telemetry` and runs
   without a route to Snowflake's telemetry host.
4. **Order.** Whether option C may ship first, since it adds no dependency to the app.
5. **`session.sql`.** Whether it stays refused in A or goes through the existing translator, with the
   owning workstream's agreement.
6. **pandas on Snowflake.** Confirmation that it stays out of scope on this PC.

## Open questions

- Whether UDTFs and UDAFs now work in local testing. The 1.58.0 source has mock classes for them, but
  the documentation lists them as unsupported.
- What `copy_into_location` and `file.get` do in local testing.
- Which of the about 462 functions run. Only a coverage run on a pinned version can say.
