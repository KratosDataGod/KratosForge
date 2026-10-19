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
