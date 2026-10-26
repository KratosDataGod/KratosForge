# Engines and connection profiles (Preview)

KratosForge is growing from "this PC or Fabric" into one place to develop for Microsoft Fabric,
Azure Databricks, or both. This page describes the two foundations that work is built on, what
they can do today, and what is planned. **Nothing on this page has a screen in the app yet**: it
is the engine underneath, built and tested first so the screens can't promise more than it does.

- An **engine** is what runs a notebook cell: Python on this PC, DuckDB, SQLite, Polars, and
  later Fabric Spark, Databricks Connect and Databricks SQL warehouses.
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
| Translated | The code is rewritten for another engine first. Spark SQL on DuckDB is translated by sqlglot. |
| Unsupported | Not available. Anything not listed is unsupported. |

### Compatibility matrix

"Tested" means automated tests in this repository, on Linux and Windows CI, with no cloud. Nothing
here has run against a Fabric tenant or a Databricks workspace.

| Engine | Where it runs | Languages | SQL | Cancel | Plan | Variables | Billed | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Python (this PC) | This PC, its own process | Python native; PySpark simulated | (use a SQL engine) | Native | No | Native | Nothing | Tested |
| DuckDB (this PC) | This PC | SQL | DuckDB native; Spark SQL translated | Native | Native | No | Nothing | Tested |
| SQLite (this PC) | This PC | SQL | SQLite native | Native | Native | No | Nothing | Tested |
| Polars SQL (this PC) | This PC | SQL | Polars SQL partial | **No** (the query finishes) | Native | No | Nothing | Tested |
| Apache Spark (this PC) | This PC | none yet | none yet | | | | Nothing | Planned |
| Fabric Spark (remote) | Fabric capacity | none yet | none yet | | | | Fabric capacity | Planned, Preview |
| Databricks Connect (remote) | Databricks compute | none yet | none yet | | | | DBUs | Planned, Preview |
| Databricks SQL warehouse (remote) | Databricks | none yet | none yet | | | | DBUs | Planned, Preview |

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
unit seconds and Databricks DBUs are different units, priced differently: KratosForge never adds
them together or converts one into the other. Comparing money needs your pricing, in Optimize.

## Connection profiles

A profile needs only a name and a kind: **This PC only**, **Microsoft Fabric**, **Azure
Databricks**, or **Fabric and Databricks**. Everything else has a sensible default:

| Part | Default | What it's for |
| --- | --- | --- |
| Connections | The app's own Fabric sign-in | One per platform: Fabric workspace; Databricks workspace address, catalog and schema |
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

A service principal's secret is named as `env:NAME` (an environment variable) or `credman:TARGET`
(Windows Credential Manager). KratosForge refuses to save or import a profile with anything that
looks like a token, key or password in it, and it never repeats the value in its error.

For Fabric and Databricks together, each platform keeps its own sign-in, workspaces and deploy
targets, and the two can't share a secret.

### Databricks CLI profiles

If you already use the Databricks CLI, KratosForge reads the profile names and workspace addresses
in `~/.databrickscfg` (or `DATABRICKS_CONFIG_FILE`) so you can pick one. It never reads out tokens or
client secrets. A profile that keeps a personal access token in that file gets a warning, because
`databricks auth login` keeps tokens out of plain text. Only Azure Databricks is supported for now.

### Sharing a profile

Export writes a profile without anything tied to your PC: secret references, the Databricks CLI
profile name and the last check are removed. Whoever imports it adds their own sign-in; the
imported profile says what's missing.

### Switching profiles

Switching closes every open engine session (they may hold data read with the old sign-in). Cells
and outputs stay. A notebook with unsaved changes must be saved or discarded first.

## Not yet

- No screens: the notebook's engine picker and profile setup come next, after a mockup.
- No Databricks sign-in, workspace discovery or execution yet; those need the Databricks SDK and
  will be labeled Preview until tested against a live workspace.
- Databricks Connect will run in its own Python environment: it ships its own PySpark, which would
  otherwise change what `spark` means in every local notebook. Its version is checked before
  anything starts (Databricks Runtime 16.4 or later, or serverless, for this app's Python 3.12).
