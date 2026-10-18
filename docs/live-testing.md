# Live testing on your tenant

Every automated test of Fabric Desktop by KratosBI fakes Fabric. The live test is the one that calls it: it signs in, creates a throwaway lakehouse in a workspace you set aside for tests, writes and reads OneLake files and Delta tables, checks that errors are reported, deletes what it created, and writes a report. Use it to prove the app works with your tenant's settings before a pilot, after a tenant setting changes, and before moving a build to Stable.

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

## Results

Each run gets an id and a folder, `live-test-results/<run id>/`:

- `summary.md`: a table of every check with its result, time and detail. Attach it to a bug report or a pilot sign-off.
- `result.json`: the same as data (`"schema": "live-test-result@1"`), for comparing runs.
- `ledger.json`: everything the run created that still exists. Normally empty at the end.

Neither file holds a token: anything token-shaped in an error message is replaced with `[removed]`. They do hold the workspace's name and id and your tenant id, so share them as you would those.

## If cleanup didn't finish

If the run was stopped, or deleting failed, the summary lists what was **left behind**. Finish it with:

```powershell
fabric-desktop live-test --cleanup live-test-results\<run id>\ledger.json
```

It deletes only what that ledger lists, only in that workspace, and only things named `fdlt_<run id>`. You can also delete a `fdlt_...` lakehouse in Fabric by hand.

If your tenant has item recovery turned on, Fabric may keep a deleted lakehouse recoverable for a while; it still counts toward the workspace's item limit until it is purged.

## What it doesn't check yet

- Running notebooks in Fabric (Run in Fabric, schedules) and pipelines.
- Two writers committing to one table at once ([roadmap FD-2](enterprise-roadmap.md#work-items)).
- Shortcuts, data access roles and the platform's SQL database.

These need longer runs or capacity time; they will be added as separate, opt-in checks.

## T-SQL contract cases

Fabric Desktop by KratosBI never runs T-SQL on this PC: it checks and formats it ([SQL compatibility check](sql-compatibility.md)) and runs Spark SQL and DuckDB here. The T-SQL contract cases (HD-054) pin that behavior and say what Fabric is expected to do with the same SQL, so a change on either side shows up.

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
