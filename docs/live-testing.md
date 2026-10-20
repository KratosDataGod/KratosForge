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

These take longer, create items besides the lakehouse, and use capacity, so the default run leaves them out. Add `--all` to run the first three, or name one or more with `--check <id>`:

```powershell
fabric-desktop live-test --workspace <workspace id> --all --dry-run              # lists them, with what each writes
fabric-desktop live-test --workspace <workspace id> --check delta.concurrent
fabric-desktop live-test --workspace <workspace id> --all --run-in-fabric        # also runs a notebook in Fabric
```

| Check id | What it does and writes | What it proves |
| --- | --- | --- |
| `delta.concurrent` | Two writers append 50 rows each at the same moment to `dbo.livetest_concurrent` in the test lakehouse, through the same write path notebooks and jobs use. Writes to OneLake, which uses capacity. | Both commits land (version 2), the row count is the sum, no row is lost. The detail says which writer had to retry after losing the commit race, if either did. |
| `notebook.publish` | Publishes a two-cell notebook item `fdlt_<run id>_nb` the way **Publish to Fabric** does, reads its definition back and compares the cells. Creating an item uses capacity. | Publishing and reading notebook definitions work with your tenant. |
| `pipeline.definition` | Creates a DataPipeline item `fdlt_<run id>_pl` with one Wait activity, reads its definition back, parses it and exports it again. Creating an item uses capacity. | The app reads Fabric's pipeline definitions and round-trips them without loss. |
| `notebook.run` | Runs `fdlt_<run id>_nb` in Fabric the way **Run in Fabric** does and waits up to 30 minutes (then cancels it). Spends Spark capacity. | Fabric's job scheduler runs a notebook the app published. |

`notebook.run` only runs with `--run-in-fabric` (which also publishes the notebook). Without it the result says `skipped: needs --run-in-fabric`. Your organization's policy can block runs in Fabric; the check then fails with that reason.

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

It deletes only what that ledger lists, only in that workspace, and only things named `fdlt_<run id>`. You can also delete a `fdlt_...` lakehouse, notebook or pipeline in Fabric by hand.

If your tenant has item recovery turned on, Fabric may keep a deleted lakehouse recoverable for a while; it still counts toward the workspace's item limit until it is purged.

## What it doesn't check yet

- Schedules, and running pipelines (the pipeline check reads and writes a definition; it doesn't run it).
- Shortcuts, data access roles and the platform's SQL database.

These need longer runs or capacity time; they will be added as more opt-in checks.
