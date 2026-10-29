# Engines and connection profiles (Preview)

KratosForge is growing from "this PC or Fabric" into one place to develop for Microsoft Fabric,
Azure Databricks and Snowflake, alone or together. This page describes the two foundations that work is built on, what
they can do today, and what is planned. **Nothing on this page has a screen in the app yet**: it
is the engine underneath, built and tested first so the screens can't promise more than it does.

- An **engine** is what runs a notebook cell: Python on this PC, DuckDB, SQLite and Polars, and,
  as Preview engines tested against fakes, a Databricks SQL warehouse, Databricks Connect on a
  cluster and a Fabric Spark session.
- A **connection profile** says which platforms a piece of work uses, where, how it signs in,
  which engine it runs on by default, and whether it may start remote compute.

## What each engine can do

Every engine states each capability at one of five levels, with the evidence behind it. KratosForge
refuses to register an engine that claims more than it has an adapter and tests for.

| Level | Meaning |
| --- | --- |
| Native | The engine does it itself, as the platform does. |
| Partial | It does it, with gaps the note names. |
| Simulated | A stand-in runs on this PC. PySpark here is DuckDB's PySpark API, not Spark. |
| Translated | The code is rewritten for another engine first. Spark SQL and Snowflake SQL on DuckDB are translated by sqlglot. |
| Unsupported | Not available. Anything not listed is unsupported. |

### Compatibility matrix

"Tested" means automated tests in this repository, on Linux and Windows CI, with no cloud. Nothing
here has run against a Fabric tenant or a Databricks workspace yet: the Databricks SQL warehouse,
Databricks Connect and Fabric Spark engines are tested against fakes of the platforms' documented
APIs and clients, and stay Preview until they have.

| Engine | Where it runs | Languages | SQL | Cancel | Plan | Variables | Billed | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Python (this PC) | This PC, its own process | Python native; PySpark simulated | (use a SQL engine) | Native | No | Native | Nothing | Tested |
| DuckDB (this PC) | This PC | SQL | DuckDB native; Spark SQL, Databricks SQL and Snowflake SQL translated | Native | Native | No | Nothing | Tested |
| SQLite (this PC) | This PC | SQL | SQLite native | Native | Native | No | Nothing | Tested |
| Polars SQL (this PC) | This PC | SQL | Polars SQL partial | **No** (the query finishes) | Native | No | Nothing | Tested |
| Apache Spark (this PC) | This PC | none yet | none yet | | | | Nothing | Planned |
| Fabric Spark (remote) | A Spark session on your Fabric capacity | PySpark, Python, Spark SQL, Scala native | Spark SQL native | Native | No (df.explain) | No | Fabric capacity | Preview, tested against fakes |
| Databricks Connect (remote) | Your Databricks cluster (Python on this PC) | PySpark native; Python partial | (use spark.sql) | Partial (Spark jobs) | No (df.explain) | No | DBUs | Preview, tested against fakes |
| Databricks SQL warehouse (remote) | Your Databricks SQL warehouse | SQL | Databricks SQL native | Native | Partial (EXPLAIN on the warehouse) | No | DBUs | Preview, tested against fakes |
| Snowflake SQL (remote) | Your Snowflake warehouse | none yet | none yet | | | | Snowflake credits | Planned, Preview |
| Snowpark (this PC, simulated) | This PC, its own process | none yet | none yet | | | | Nothing | Planned |
| Snowpark Connect (remote) | Your Snowflake warehouse | none yet | none yet | | | | Snowflake credits | Planned, Preview |

Engines on this PC bill nothing themselves. **Reading or writing OneLake from this PC still uses
Fabric capacity**, so "runs on this PC" never means "uses no capacity".

A session's database file (an existing DuckDB or SQLite file) is opened read-only. Each session is
its own in-memory database or Python process: two notebooks never see each other's tables or
variables, and closing a session frees everything it held.

A SQL session never reaches past its own database. DuckDB refuses COPY, EXPORT, ATTACH, INSTALL and
LOAD before anything in the cell runs (the same statements the SQL query page refuses), and its
connection can't read or write files on this PC, load extensions or reach the network at all.
SQLite refuses ATTACH and DETACH, so a cell can't create or open another database file. Polars
reads the files a cell names with read_parquet or read_csv, and never writes one.

The Python engine uses the notebook's own session (the same process **Stop** ends), so it needs
**Run notebooks in their own process** turned on in Settings. With it off every notebook shares one
kernel, and a notebook sharing another notebook's session shares its process too; in both cases the
engine says what to change instead of running.

## Snowflake SQL on this PC (Preview)

The DuckDB engine runs Snowflake SQL translated, so Snowflake queries can be tried on this PC without
a Snowflake account or a running warehouse. No screen can pick Snowflake SQL yet: this is the engine
underneath, and the Snowflake screens come with the warehouse work (SF-02). **DuckDB is not Snowflake**: nothing here has
been compared with a Snowflake account, and "it ran" never means "Snowflake gives the same answer".

Every statement in the cell gets a label, and the label is the cell's first output, before the
result:

- **Translated**: no known difference applies. It matches Snowflake's documented behaviour as far
  as KratosForge knows.
- **Runs, with a difference**: it runs, and the note says what differs and what to do about it.
- **Runs only in Snowflake**: streams, tasks, dynamic tables, Snowflake Scripting, time travel,
  clones, stages and the rest below. The note says why.

A cell runs whole or not at all: if any statement runs only in Snowflake, nothing in the cell runs.
A cell that can't be read as Snowflake SQL at all shows only the error, with no label.
The same session rules apply as for any DuckDB cell (no files, extensions or network). **Plan**
shows DuckDB's plan for the translated SQL, not Snowflake's.

The rules and differences come from a probe of 29 common Snowflake statements on the pinned sqlglot
and DuckDB versions; each probe statement is a test.

<!-- Generated from snowflake_sql.docs_table(); a test keeps this table equal to the code. -->
Rules 2026.10.1.

| Id | What | Applies to |
| --- | --- | --- |
| SFX-001 | Rewritten | LATERAL FLATTEN and TABLE(FLATTEN(...)) become DuckDB's json_each (INDEX, VALUE, KEY, PATH; OUTER as a left join). SEQ, THIS, RECURSIVE and MODE aren't rewritten. |
| SFX-002 | Rewritten | TO_VARCHAR and TO_CHAR with a date or time format written in the query become strftime. Elements with no equivalent (quarter, week) aren't rewritten. |
| SFX-003 | Runs only in Snowflake | TO_VARCHAR and TO_CHAR with a number format. |
| SFX-004 | Rewritten | OBJECT_CONSTRUCT drops a pair whose value is SQL NULL and keeps JSON null, as Snowflake does. OBJECT_CONSTRUCT(*) isn't rewritten. |
| SFX-000 | Runs only in Snowflake | Anything else sqlglot can't write for DuckDB. |
| SFD-001 | Runs, with a difference: Unquoted names keep their case | Snowflake stores an unquoted name in upper case (MyCol becomes MYCOL); DuckDB keeps it as typed. |
| SFD-002 | Runs, with a difference: TIMESTAMP_TZ loses its offset | DuckDB stores TIMESTAMP_TZ as TIMESTAMPTZ, an instant, so the offset Snowflake keeps with each value is lost. |
| SFD-003 | Runs, with a difference: Division returns DOUBLE | DuckDB returns DOUBLE for /, where Snowflake returns NUMBER with its own scale rules. |
| SFD-004 | Runs, with a difference: Local time zone is this PC's | TIMESTAMP_LTZ and CURRENT_TIMESTAMP use this PC's time zone here, not Snowflake's TIMEZONE parameter. |
| SFD-005 | Runs, with a difference: FLATTEN rows and keys | FLATTEN runs as DuckDB's json_each: rows come back in no set order, KEY is the position for array elements (Snowflake gives NULL) and PATH is written differently. |
| SFN-001 | Runs only in Snowflake | Streams. |
| SFN-002 | Runs only in Snowflake | Tasks. |
| SFN-003 | Runs only in Snowflake | Dynamic tables (their SELECT can be tried on its own). |
| SFN-004 | Runs only in Snowflake | Snowflake Scripting, procedures, user-defined functions and CALL. |
| SFN-005 | Runs only in Snowflake | EXECUTE IMMEDIATE. |
| SFN-006 | Runs only in Snowflake | Time travel: AT and BEFORE. |
| SFN-007 | Runs only in Snowflake | UNDROP and zero-copy CLONE. |
| SFN-008 | Runs only in Snowflake | Stages: @stage references, COPY INTO, PUT, GET, LIST, REMOVE. |
| SFN-009 | Runs only in Snowflake | USE, SHOW and DESCRIBE. |
| SFN-010 | Runs only in Snowflake | GRANT, REVOKE, and ALTER of Snowflake settings (policies, tags, suspend and resume). |
| SFN-011 | Runs only in Snowflake | Snowflake-only objects: stages, pipes, warehouses, roles, users, shares, integrations, file formats, sequences, policies, tags. |
| SFN-012 | Runs only in Snowflake | Cortex AI functions. |
| SFN-013 | Runs only in Snowflake | TIMESTAMP_TZ text with an offset after a space; DuckDB reads it as a time zone name. |
| SFN-099 | Runs only in Snowflake | Any other Snowflake command sqlglot can't read as a statement. |

## Databricks SQL warehouse (Preview)

SQL cells can run on a Databricks SQL warehouse in your workspace, through Databricks' documented
Statement Execution API. A cell may hold several statements; they run in order and the last one's
result is shown, with the warehouse's exact row count. **Cancel** stops the statement on the
warehouse. **Catalog** lists Unity Catalog catalogs, schemas and tables without starting the
warehouse. **Plan** runs EXPLAIN on the warehouse.

- **It bills DBUs.** A statement sent to a stopped warehouse starts it, and a running warehouse
  bills Databricks units until its own auto-stop. KratosForge never stops your warehouse and never
  sends a cell there without your confirmation or your profile's policy.
- **Sign-in** comes from where you already keep it: a Databricks CLI profile (signed in with
  `databricks auth login`, or holding a service principal or personal access token), browser
  sign-in through the Databricks CLI, or a service principal whose secret is an `env:` or `credman:`
  reference. The token stays in memory for the session and is never written anywhere.
- **Cells can change data.** Once the session is open, a cell's CREATE, INSERT, MERGE or DROP runs
  on the warehouse with no further confirm, as in a Databricks notebook. The confirm is for starting
  the session, not for each statement.
- **Try it on this PC first.** The DuckDB engine runs Databricks SQL translated by sqlglot, so you can
  write and test a warehouse query against local tables without starting the warehouse. Unity
  Catalog three-part names need that catalog and schema in the local session, and Delta-only
  commands don't run there.
- **Limits:** a statement still running after two hours is cancelled. Results come back inline, so the warehouse refuses one over 25 MB (add a LIMIT), and
  only the first result chunk is previewed. Billed DBUs are in Databricks' system billing tables,
  per warehouse and hour, so the engine reports measured seconds only.

### Databricks SQL functions on this PC

The DuckDB engine's own translation (sqlglot, then DuckDB) runs each check against the value
Databricks gives: its documented example, or one worked by hand where the reference gives none. A
test runs them again, so the table changes when an upgrade fixes or breaks one. A function not listed
here or below hasn't been checked.

Of 138 checks on commonly used Databricks SQL built-in functions, these are the functions the DuckDB engine doesn't answer the same way.

| Function | Group | On this PC | What to know |
| --- | --- | --- | --- |
| `format_number` | String | Doesn't run | DuckDB has no format_number; use printf or format. |
| `soundex` | String | Doesn't run | DuckDB has no soundex. |
| `bround` | Math | Doesn't run | DuckDB has no banker's rounding function. |
| `conv` | Math | Doesn't run | DuckDB has no base conversion function. |
| `exists` | Array and map | Doesn't run | The lambda isn't translated to DuckDB's syntax. |
| `percentile_approx` | Aggregate | Different result | Approximate on both, but the estimates differ. |

These 132 checks answer the same as on Databricks: `concat`, `concat_ws`, `upper`, `lower`, `length`, `substring`, `substr`, `trim`, `ltrim`, `rtrim`, `lpad`, `rpad`, `replace`, `instr`, `locate`, `position`, `split`, `split_part`, `regexp_replace`, `regexp_extract`, `regexp_like`, `initcap`, `reverse`, `repeat`, `left`, `right`, `startswith`, `endswith`, `contains`, `md5`, `sha2`, `base64`, `ascii`, `chr`, `translate`, `levenshtein`, `abs`, `round`, `round (negative half)`, `ceil`, `floor`, `mod (%)`, `pmod`, `div`, `power`, `sqrt`, `ln`, `log10`, `log`, `greatest`, `least`, `sign`, `try_divide`, `hex`, `bin`, `factorial`, `date_add`, `date_sub`, `datediff`, `add_months`, `last_day`, `year`, `month`, `dayofmonth`, `dayofweek`, `weekofyear`, `quarter`, `date_format`, `to_date`, `to_timestamp`, `date_trunc`, `trunc`, `make_date`, `next_day`, `months_between`, `array`, `size`, `array_contains`, `element_at`, `array_distinct`, `array_sort`, `sort_array`, `array_join`, `array_max`, `array_min`, `flatten`, `slice`, `sequence`, `array_position`, `arrays_overlap`, `array_union`, `array_intersect`, `array_except`, `transform`, `filter`, `aggregate`, `explode`, `map_keys`, `map_values`, `coalesce`, `nvl`, `ifnull`, `nullif`, `nvl2`, `if`, `iff`, `decode`, `get_json_object`, `to_json`, `json_array_length`, `count_if`, `collect_list`, `collect_set`, `array_agg`, `max_by`, `min_by`, `bool_and`, `bool_or`, `median`, `percentile`, `approx_count_distinct`, `stddev`, `variance`, `any_value`, `sort_array (descending)`, `arrays_overlap (only NULL could match)`, `array_union (NULLs)`, `pmod (negative divisor)`, `dayofweek (Sunday)`, `array_intersect (order and NULL)`, `array_except (order and NULL)`, `array_distinct (order and NULL)`.

## Databricks Connect (Preview)

PySpark cells can run their `spark` work on a Databricks cluster through Databricks Connect, while
the rest of the cell's Python runs on this PC.

- **Its own environment.** Databricks Connect brings its own PySpark, so it never loads inside
  KratosForge. You install it once per Databricks Runtime line (for example 16.4); KratosForge
  downloads it with pip into a folder of its own and runs each session as a separate process there.
  Opening a session never installs anything.
- **Pinned packages only.** Each supported runtime line (16.4 and 17.3 in this version) has a lock
  file in the app naming every package, its exact version and its hash. pip installs only those
  files, as prebuilt wheels, and refuses any whose hash differs, so a changed or look-alike package
  never installs. Other runtime lines say they aren't supported in this version. Your organization
  can point the download at its own package mirror (`dbconnect_index_url`) or turn installs off
  (`allow_dbconnect_install`) in the [policy file](azure-setup.md).
- **Checked first.** Before anything starts, KratosForge reads the cluster's runtime and checks it
  against the installed client and this PC's Python. KratosForge's Python is 3.12, so clusters on
  Databricks Runtime 16 and later work. 13.3 to 15.4 need Python 3.10 or 3.11, and the check says so.
- **It bills DBUs.** Connecting to a terminated cluster starts it, and a running cluster bills
  Databricks units until it auto-terminates. A session opens only after your confirmation for that
  cluster, or your profile's policy. Closing the session ends the process on this PC, not the cluster.
- **Sign-in** goes to the process as the Databricks SDK's own settings: your CLI profile's name, or
  the workspace address with the CLI's sign-in, or a service principal whose secret is read from its
  reference. Databricks variables already set on this PC are left out. A sign-in through the CLI
  needs the Databricks CLI on PATH; without it the session doesn't start. A service principal's
  secret is removed from the process once the session has signed in, but cells still run with that
  principal's rights in the workspace, so give it only the access the notebook needs.
- **Limits:** Spark Connect has no RDDs or SparkContext. Results show the first rows only, because
  counting them all would run another Spark job. Cancel stops the session's Spark jobs, not plain
  Python. Serverless compute isn't offered yet.

## Fabric Spark (Preview)

Cells can run in an interactive Spark session in your Fabric workspace, on a lakehouse you pick,
through Fabric's Livy API with your own Fabric sign-in. PySpark, Python, Spark SQL and Scala cells
run in the same session, as in a Fabric notebook. Each cell is sent once and never retried, so it
can't run twice. **Cancel** cancels the running statement in Fabric, and closing the session ends it.

- **It uses Fabric capacity** for as long as the session is open, including while it sits idle,
  until you close it or Fabric ends it. A session opens only after your confirmation for that
  lakehouse, or your profile's policy, and never when your organization has turned off Fabric runs
  from KratosForge.
- **Limits:** output arrives when the cell finishes; notebookutils and %magics are Fabric notebook
  features a Livy session doesn't fully have; capacity use is in the Fabric Capacity Metrics app, not
  per cell. A cell still running after two hours is cancelled.

## Switching engines

A notebook has one engine at a time. Switching to another engine starts a new session: cells and
outputs stay, but variables and tables in the old session are not carried over. KratosForge asks
first, with two choices: close the old session, or keep it in the background so switching back
finds it (local engines only; a remote session is never left running).

If a cell can't run on the chosen engine, KratosForge says why and stops. **It never moves the cell
to another engine on its own**, and above all never to a remote one.

## Remote compute needs your say-so

A remote engine can start compute that is billed. It opens only when:

- you confirm it for that engine and that workspace, cluster or warehouse (the confirmation lasts
  an hour), or
- the connection profile's execution policy allows that engine without asking.

A profile can also forbid remote compute altogether.

## Usage figures

Every run records what it used, labeled as one of: **measured** on this PC, **billed** by the
platform (with where the bill came from), or **estimated** (with its assumptions). Fabric capacity
unit seconds, Databricks DBUs and Snowflake credits are different units, priced differently:
KratosForge never adds them together or converts one into the other. Comparing money needs your pricing, in Optimize.

## Connection profiles

A profile needs only a name and a kind: **This PC only**, **Microsoft Fabric**, **Azure
Databricks**, **Snowflake**, or **Two or more platforms** (hybrid). Everything else has a sensible
default:

| Part | Default | What it's for |
| --- | --- | --- |
| Connections | The app's own Fabric sign-in | One per platform: Fabric workspace; Databricks workspace address, catalog and schema; Snowflake account, user, role, warehouse, database and schema |
| Default engine | DuckDB on this PC | What a new notebook runs on |
| Environments | Dev, Test, Staging, Prod, all empty | Which workspace, catalog or schema each environment uses, per connection |
| Deploy targets | none | Where each environment deploys, per connection (a Fabric workspace, a Databricks bundle target) |
| Execution policy | Ask before remote compute | Ask, allow listed engines, or never; an optional daily remote-minutes budget |
| Data access | Ask before copying between platforms | Data never moves between Fabric and Databricks on its own |
| Validation | Not checked | The last check of the profile |

The app's current behaviour is the built-in profile **This PC and Fabric**.

### Sign-in: references, never secrets

A profile never holds a password, token or key. It holds a *reference* to one:

| Platform | Ways to sign in |
| --- | --- |
| Fabric | The app's own sign-in (default); a service principal, managed identity or workload identity for automation |
| Databricks | A profile from the Databricks CLI's config file; browser sign-in (OAuth); a service principal (OAuth machine-to-machine) |
| Snowflake (planned) | Browser sign-in through Snowflake's built-in local application (default); a key pair (the private key file's path, and its passphrase as a reference); a programmatic access token (as a reference); a connection from the Snowflake CLI |

A service principal's secret is named as `env:NAME` (an environment variable) or `credman:TARGET`
(Windows Credential Manager). KratosForge refuses to save or import a profile with anything that
looks like a token, key or password in it, and it never repeats the value in its error.

In a hybrid profile, each platform keeps its own sign-in, workspaces and deploy targets, and no two
platforms can share a secret.

A Snowflake connection names the account by its identifier (`myorg-myaccount`, or a legacy locator
such as `xy12345.east-us-2.azure`), never by a URL; the address is always
`https://<account>.snowflakecomputing.com`. On Windows a private key file must be a full path on a
drive (`C:\keys\snowflake.p8`): a network share path is refused, so reading the key never reaches
another computer. Nothing signs in to Snowflake yet; the sign-in methods are recorded so a profile
made now keeps working when they arrive.

Profiles that use only Fabric and Databricks are still saved in the format older versions of
KratosForge read, so a profile shared with someone who hasn't updated still opens for them. A
profile with Snowflake, or any other mix of platforms, needs this version or later.

### Databricks CLI profiles

If you already use the Databricks CLI, KratosForge reads the profile names and workspace addresses
in `~/.databrickscfg` (or `DATABRICKS_CONFIG_FILE`) so you can pick one. It never reads out tokens or
client secrets. A profile that keeps a personal access token in that file gets a warning, because
`databricks auth login` keeps tokens out of plain text. Only Azure Databricks is supported for now.

### Snowflake CLI connections

If you already use the Snowflake CLI, KratosForge reads its connection names, accounts, users,
roles, warehouses, databases and schemas so you can pick one. It looks where the CLI does:
`SNOWFLAKE_HOME` if it's set (if that folder doesn't exist it finds nothing, as the CLI does), else
`~/.snowflake`, else `%USERPROFILE%\AppData\Local\snowflake`, and `connections.toml` there wins
over the connections in `config.toml`. It never reads out passwords, tokens, private keys or
passphrases. A connection that keeps one in the file gets a warning, and a file it can't read says
only that, never which line was wrong. A connection that signs in with browser single sign-on is
listed but can't be used yet.

### Sharing a profile

Export writes a profile without anything tied to your PC: secret references, the Databricks CLI
profile name, the Snowflake CLI connection name, the private key file's path and the last check are
removed. Whoever imports it adds their own sign-in; the
imported profile says what's missing.

### Switching profiles

Switching closes every open engine session (they may hold data read with the old sign-in). Cells
and outputs stay. A notebook with unsaved changes must be saved or discarded first.

## Adding an engine

Every engine with an adapter must pass the shared contract checks in
`sidecar/tests/engine_conformance.py`, run by `tests/test_engine_conformance.py`. The checks read the
engine's own descriptor and hold the adapter to it: claimed capabilities work, unsupported ones
raise `Unsupported` with a reason, a cell in a language or dialect the engine marks unsupported never
comes back as run, a failing cell is an error output that never repeats a secret, a closed session
refuses cells, two sessions share nothing, and a remote engine contacts nothing before it has an
authorization. Remote engines run against the same fakes as their own tests, so the checks never
reach a workspace.

A new adapter adds a harness there: how to open a session (against a fake for a remote engine), a
cell that runs, a cell that fails, and cells it refuses. The run fails until every engine with an
adapter has one.

## Not yet

- No screens: the notebook's engine picker and profile setup come next, after a mockup.
- Snowflake: the three engines above are listed so the plan is visible, but none runs a cell yet.
  Snowflake SQL and Snowpark Connect will run on your Snowflake warehouse and use credits, so they
  will ask first, as every remote engine does. Snowpark on this PC will be simulated by Snowflake's
  local testing framework, which can't run `session.sql` and implements only some functions.
- Databricks SQL warehouses sign in through the Databricks CLI or a service principal and run SQL
  through the Statement Execution API, tested against fakes only. Databricks workspace discovery and
  Databricks Connect aren't built yet, and stay Preview until tested against a live workspace.
- Databricks Connect will run in its own Python environment: it ships its own PySpark, which would
  otherwise change what `spark` means in every local notebook. Its version is checked before
  anything starts (Databricks Runtime 16.4 or later, or serverless, for this app's Python 3.12).
