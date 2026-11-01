# Live testing on your tenant

Every automated test of KratosForge fakes Fabric. The live test is the one that calls it: it signs in, creates a throwaway lakehouse in a workspace you set aside for tests, writes and reads OneLake files and Delta tables, checks that errors are reported, deletes what it created, and writes a report. Use it to prove the app works with your tenant's settings before a pilot, after a tenant setting changes, and before moving a build to Stable.

It never touches production on purpose, and it refuses to start anywhere that might be production.

## Before you start

1. **Create a workspace for tests** on an active capacity, or pick an existing non-production one. Don't use a workspace with data anyone needs.
2. **Mark it for tests.** In Fabric, open the workspace's **Workspace settings > General** and add `fabric-desktop-live-test` anywhere in the description. Only a workspace admin can change it, which is the point: the live test refuses any workspace without it.
3. **Give yourself (or the agent's identity) Contributor or higher** on that workspace. Deleting what the test created needs write access to those items.
4. Copy the workspace's id from its URL: `https://app.fabric.microsoft.com/groups/<workspace id>/...`.

The live test also refuses a workspace whose name contains "prod", "prd" or "production" as a word, and any workspace your organization's [policy file](azure-setup.md) names as a Platform workspace, even when it is marked.

## Run it

Open a terminal where the engine is installed (from source: `sidecar/` after `pip install -e .`):

```powershell
fabric-desktop live-test --workspace <workspace id> --dry-run   # lists what it would do; signs in to nothing, writes nothing
fabric-desktop live-test --workspace <workspace id>             # asks "Run it?", then runs
```

Add `--yes` to skip the question (for a scheduled run), `--out <folder>` to choose where results go (default `live-test-results`), and `--agent-config <agent.toml>` to sign in as the [headless agent's](agent.md) service principal or managed identity instead of as you.

The exit code is 0 when every check passed, 1 when one failed, and 2 when the workspace was refused or the run wasn't started.

## What it checks

| Check | What it proves |
| --- | --- |
| Get a token for the Fabric API | Sign-in works with your tenant's Conditional Access and app policies |
| Get a token for OneLake | The OneLake (Azure Storage) audience is allowed |
| Read the workspace | You can see the workspace; the safety checks run here, before anything is written |
| Create a schema-enabled lakehouse | Item creation, long-running operations, `Item.ReadWrite.All` |
| Read the lakehouse's properties | The lakehouse reports its OneLake paths |
| Write, list, read and delete a file in OneLake | The tenant setting for apps outside Fabric is on; DFS create, append, flush, list, read and delete work |
| Write a Delta table to OneLake | delta-rs commits to OneLake by GUID path |
| Read the Delta table back | The rows come back unchanged |
| Append rows | A second commit makes version 1 |
| Merge | Delta MERGE (update one row, insert one) works on OneLake, as the metadata framework's incremental loads need |
| Read version 0 | Time travel, which table history and restore use |
| A missing lakehouse is reported as an error | Fabric errors reach the app as errors with their code, not as empty results |
| OneLake refuses a bad token | A request with a bad token fails with 401 or 403 instead of passing silently |
| Delete the lakehouse | Cleanup works; the lakehouse can no longer be read |

A check that depends on an earlier one that failed is marked Skipped, with the reason.

## Opt-in checks

These take longer, create items besides the lakehouse, and use capacity, so the default run leaves them out. Add `--all` to run all but `notebook.run`, or name one or more with `--check <id>`:

```powershell
fabric-desktop live-test --workspace <workspace id> --all --dry-run              # lists them, with what each writes
fabric-desktop live-test --workspace <workspace id> --check delta.concurrent
fabric-desktop live-test --workspace <workspace id> --all --run-in-fabric        # also runs a notebook in Fabric
```

| Check id | What it does and writes | What it proves |
| --- | --- | --- |
| `delta.concurrent` | Two writers append 50 rows each at the same moment to `dbo.livetest_concurrent` in the test lakehouse, through the same write path notebooks and jobs use. Writes to OneLake, which uses capacity. | Both commits land (version 2), the row count is the sum, no row is lost. The detail says which writer had to retry after losing the commit race, if either did. |
| `lease.onelake` | Holds a table write lease on `dbo.livetest_lease` (a small file under `Files/_fabric_desktop/leases` in the test lakehouse) and, while it is held, has a second writer try to take it, replace it with a stale ETag and wait for it. A few small OneLake writes, which use capacity. | OneLake honours the conditional requests (`If-None-Match: *`, `If-Match`) that per-table write leases rely on, so two KratosForge writers never write one table at once. If it fails, two writers can overlap on that tenant. |
| `delta.merge_profile` | Writes `dbo.livetest_merge`, then runs `MERGE INTO ... WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *` on it the way the app's SQL does, under the table's lease. Writes 4 rows to OneLake, which uses capacity. | The MERGE lands (one row updated, one inserted) and the table stays in the Fabric-safe Delta profile (reader 1, writer 2, no deletion vectors), so Fabric's SQL analytics endpoint and Direct Lake still read it. |
| `notebook.publish` | Publishes a two-cell notebook item `fdlt_<run id>_nb` the way **Publish to Fabric** does, reads its definition back and compares the cells. Creating an item uses capacity. | Publishing and reading notebook definitions work with your tenant. |
| `pipeline.definition` | Creates a DataPipeline item `fdlt_<run id>_pl` with one Wait activity, reads its definition back, parses it and exports it again. Creating an item uses capacity. | The app reads Fabric's pipeline definitions and round-trips them without loss. |
| `pipeline.mailbox` | Writes a run request under `Files/_fabric_desktop` in the test lakehouse. Two in-memory agents read it: the first takes it, the second must leave it alone, and a cancel file ends it `cancelled`. The request names a pipeline run that doesn't exist, which must be reported as not found. Nothing is run. Writes a few small files to OneLake, which uses capacity. | The [pipeline mailbox](agent.md#run-from-a-fabric-pipeline)'s conditional writes (`If-None-Match`, `If-Match`) work on OneLake, and the run lookup works with your sign-in. |
| `notebook.run` | Runs `fdlt_<run id>_nb` in Fabric the way **Run in Fabric** does and waits up to 30 minutes (then cancels it). Spends Spark capacity. | Fabric's job scheduler runs a notebook the app published. |

`notebook.run` only runs with `--run-in-fabric` (which also publishes the notebook). Without it the result says `skipped: needs --run-in-fabric`. It also stays skipped when `notebook.publish` failed, for example when the cells read back differ, so no capacity is spent running a notebook that wasn't published as written. Your organization's policy can block runs in Fabric; the check then fails with that reason.

Every item these checks create is named `fdlt_<run id>_...`, goes into the ledger the moment it exists, and is deleted in cleanup before the lakehouse. If an item with that name already exists, the check fails rather than overwrite it. The same production refusals apply.

## Results

Each run gets an id and a folder, `live-test-results/<run id>/`:

- `summary.md`: a table of every check with its result, time and detail. Attach it to a bug report or a pilot sign-off.
- `result.json`: the same as data (`"schema": "live-test-result@1"`), for comparing runs. Each check has its `id`, `status` (`passed`, `failed` or `skipped`), `seconds` and `detail`; `opt_in` lists the opt-in checks the run included.
- `ledger.json`: everything the run created that still exists. Normally empty at the end.

Neither file holds a token: anything token-shaped in an error message is replaced with `[removed]`. They do hold the workspace's name and id and your tenant id, so share them as you would those.

## If cleanup didn't finish

If the run was stopped, or deleting failed, the summary lists what was **left behind**. Finish it with:

```powershell
fabric-desktop live-test --cleanup live-test-results\<run id>\ledger.json
```

It deletes only what that ledger lists, only in that workspace, and only things named `fdlt_<run id>` or `fdlt_<run id>_...`. Before each delete it reads the item from Fabric and skips it, leaving it in the ledger, unless its type and name still match. You can also delete a `fdlt_...` lakehouse, notebook or pipeline in Fabric by hand.

If your tenant has item recovery turned on, Fabric may keep a deleted lakehouse recoverable for a while; it still counts toward the workspace's item limit until it is purged.

## The tenant smoke test

The [tenant smoke test](product/smoke-test/README.md) runs this live test with every opt-in check as one of ten core flows of the app (`fabric-desktop smoke-test`), adds sign-in, catalog and support-bundle checks, and lists the by-hand part of each flow.

## What it doesn't check yet

- Schedules, and running pipelines (the pipeline check reads and writes a definition; it doesn't run it).
- Shortcuts, data access roles and the platform's SQL database.

These need longer runs or capacity time; they will be added as more opt-in checks.

## Databricks live test

The Databricks SQL warehouse and Databricks Connect engines are in Preview and tested against fakes. This live test runs them, through the same code notebooks use, against a real Azure Databricks workspace. It signs in, lists Unity Catalog catalogs, runs queries on a SQL warehouse, runs a cell on a cluster through Databricks Connect, cancels a long statement and a long Spark job, closes both sessions, checks that errors come back as errors, and writes a report.

It creates nothing in the workspace: no tables, schemas, files, jobs or clusters. It does start compute. A stopped warehouse or a terminated cluster starts when the test uses it, and bills Databricks units (DBU) until its own auto-stop.

### Before you start

1. **Pick or create a SQL warehouse for tests.** The smallest is enough: a serverless 2X-Small with **Auto stop** set to the minimum.
2. **For Databricks Connect, pick or create a cluster** on Databricks Runtime 16 or later, in **Dedicated** (single user) access mode, with Unity Catalog. A single node is enough. Set **Terminate after** to 10 minutes. In KratosForge, install databricks-connect for its runtime from **Engines** first.
3. **Tag them for tests.** On the warehouse and on the cluster, add a custom tag with the key `fabric-desktop-live-test`. The value doesn't matter. Only someone with CAN MANAGE can add it, which is the point: the live test refuses compute without it.
4. **Sign in with the Databricks CLI:** `databricks auth login --host <workspace url> --profile kf-test`. You need CAN USE on the warehouse, CAN ATTACH TO on the cluster, and USE CATALOG on at least one catalog.

The live test also refuses a warehouse or cluster whose name contains "prod", "prd" or "production" as a word, or that has auto-stop turned off.

### Run it

```powershell
fabric-desktop live-test-databricks --profile kf-test --warehouse <warehouse id> --cluster <cluster id> --dry-run   # signs in to nothing
fabric-desktop live-test-databricks --profile kf-test --warehouse <warehouse id> --cluster <cluster id>             # asks "Run it?", then runs
```

Both commands first check this PC and print a line for each: Python 3.12 or later, the Databricks CLI on PATH at 0.205 or later (it is only asked for its version), and the profile in `~/.databrickscfg` with an Azure Databricks workspace address (or the `--host` address). A line marked `NO` says what to install or run. The dry run only reports them; a real run stops there, before signing in or starting anything, until they all pass.

Leave out `--cluster` to test only the SQL warehouse. Use `--host <workspace url>` instead of `--profile` to sign in with `databricks auth login --host`. Add `--stop-compute` to stop the warehouse and terminate the cluster afterwards. They are only stopped if they were stopped when the run began, so the test never stops compute someone else was using. Answering "Run it?" (or passing `--yes`) is the confirmation the engines need before they start remote compute.

The exit code is 0 when every check passed, 1 when one failed, and 2 when this PC wasn't ready, the warehouse or cluster was refused, or the run wasn't started.

### What it checks

| Check | What it proves |
| --- | --- |
| Get a token for the workspace | Sign-in from the CLI profile works |
| Read the SQL warehouse, read the cluster | You can see them. The safety checks run here, before anything starts. Reading never starts them |
| Open a Databricks SQL session | The engine opens on the warehouse |
| List Unity Catalog catalogs | The Catalog pane's API works with your privileges |
| Run a query on the warehouse | Statement Execution works; a stopped warehouse starts; 5 rows come back |
| A failing statement is reported as an error | The warehouse's error reaches the cell, naming the missing table |
| Cancel a running statement | Cancel stops the statement on the warehouse |
| Close the SQL session | Closing works |
| Open a Databricks Connect session | The cluster's runtime, the installed client and this PC's Python match, and the session connects |
| Run a cell on the cluster | `spark.range(5).count()` returns 5 from the cluster |
| Cancel a running Spark job | Cancel interrupts the job on the cluster |
| Close the session | The session's process on this PC ends |
| A missing warehouse is reported as an error | The workspace's errors reach the app as errors |
| The workspace refuses a bad token | A request with a bad token fails with 401 or 403 |

Results go to `live-test-results/<run id>/`, in `summary.md` and `result.json` (`"schema": "databricks-live-test-result@1"`). They name the workspace, warehouse and cluster, and never hold a token. `left_running` lists what the run started and left to its auto-stop.

### By hand

After an unattended run passes, check these in the app once, on the same warehouse and cluster:

1. On **Engines**, the Databricks SQL warehouse and Databricks Connect engines ask before starting the warehouse or cluster, and name it.
2. A SQL cell on the warehouse shows a table. Cancel on a long cell stops it.
3. The Catalog pane lists the catalogs, then a catalog's schemas, then a schema's tables, without starting a stopped warehouse.
4. A PySpark cell through Databricks Connect shows the first rows of a DataFrame. Stopping the engine ends its process (it no longer appears in Task Manager).
5. A connection profile with remote compute set to **never** refuses both engines.

## T-SQL contract cases

KratosForge never runs T-SQL on this PC: it checks and formats it ([SQL compatibility check](sql-compatibility.md)) and runs Spark SQL and DuckDB here. The T-SQL contract cases (HD-054) pin that behavior and say what Fabric is expected to do with the same SQL, so a change on either side shows up.

The cases are in `sidecar/tests/tsql_contract/cases.jsonl`, one JSON object per line after the first, and `sidecar/tests/test_tsql_contract.py` runs them with the rest of the tests. Each case has:

| Field | Holds |
| --- | --- |
| `id`, `area`, `target`, `sql` | A unique name; the area (`top`, `cast_convert`, `date`, `string`, `null_handling`, `cte`, `window`, `merge`, `unsupported`); the engine (`warehouse`, `endpoint`, `sqldb`, or `spark` for SQL run on this PC); the SQL |
| `local.check` | The compatibility check's outcome and exact rules |
| `local.format` | `formats` or `left_alone` (Format SQL left it as it was). Formatting must never change the check's findings |
| `local.run` | Spark SQL cases only: the rows, or part of the error, that running it on this PC gives. T-SQL cases are always refused here, and the test checks that |
| `local.equivalent` | Optional: the Spark SQL spelling of the same query, run on this PC, and its rows |
| `fabric` | `expect` (`accepts`, `rejects` or `unknown`), `why`, and the `rows` Fabric should return. They must equal the local equivalent's rows unless `known_difference` says why not (for example, `7 / 2` is 3 in T-SQL and 3.5 in Spark SQL) |
| `verification` | `local_pinned`, or `verified_against_fabric` with a `verified` block (`on`, `engine`, `run`: the date, the engine and a link to the live run). Every case is `local_pinned` today: passing on this PC proves nothing about Fabric |
| `xfail` | A known local bug, by part (`check`, `format_outcome`, `format_keeps_findings`, `run`, `equivalent`) and why. The test then expects that part to fail, and fails when the bug is fixed, so the case gets updated |

To add a case, add a line, run `python -m pytest -q tests/test_tsql_contract.py` in `sidecar/`, and pin what the app does. If what it does is wrong, keep the right expectation and add an `xfail` with the reason instead of pinning the wrong one. Only change `verification` from a live run's results.

### The live job

`.github/workflows/tsql-contract-live.yml` runs the same cases against a real Fabric warehouse or SQL analytics endpoint, every Monday and on demand. Each run uses Fabric capacity on the test workspace (its queries run in Fabric), so every run waits for a maintainer to approve it. Until it is set up it stops at once with "No test workspace configured" and runs nothing.

It must only ever point at a **dedicated test workspace**, never at production or a workspace with data anyone needs. The test refuses a workspace without `fabric-desktop-live-test` in its description, one whose name or item name looks like production, and any Platform workspace in the policy file, the same as the live test above. It reads the server address from Fabric for the named item in that workspace, so it can't be pointed anywhere else. It only runs cases it can prove read-only (no writes, no `SELECT ... INTO`), each in a transaction it rolls back; MERGE, CREATE and other writes stay offline.

To turn it on, a maintainer:

1. Creates the test workspace, marks it as described in [Before you start](#before-you-start), and creates a warehouse in it (or a lakehouse, for its SQL analytics endpoint).
2. Creates a service principal, adds it to the workspace as **Viewer**, and allows it to use Fabric APIs ([permissions](permissions.md#the-t-sql-contract-job-maintainers-only)).
3. Creates the GitHub environment `tsql-contract-live` (**Settings > Environments**) with **Required reviewers**, so no run, scheduled or manual, starts without a person's approval.
4. Sets the repository variables `FABRIC_TSQL_TEST_WORKSPACE_ID`, `FABRIC_TSQL_TEST_ITEM` (the warehouse or lakehouse name), `FABRIC_TSQL_TEST_TARGET` (`warehouse` or `endpoint`), `FABRIC_TSQL_TENANT_ID` and `FABRIC_TSQL_CLIENT_ID`, and `FABRIC_TSQL_CLIENT_SECRET` as an **environment secret** of `tsql-contract-live` (not a repository secret), so only an approved run can read it.

Each run uploads `tsql-contract-results.json`: every case's outcome, rows and (with tokens removed) error. A case Fabric answers differently from its `fabric` block fails the run; `unknown` cases are only recorded. Locally, the same run is `FABRIC_DESKTOP_TSQL_LIVE=1` plus the `FABRIC_DESKTOP_TSQL_*` settings named in the workflow, with `pyodbc` and ODBC Driver 18 for SQL Server installed.
