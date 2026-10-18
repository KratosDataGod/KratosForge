# Optimize: the shared optimization lakehouse

Optimize keeps optimization results in one Microsoft Fabric lakehouse that every Fabric Desktop by KratosBI installation in your organization can write to and read from. That includes findings, recommendations, applied fixes, measurements, local runs and savings. The lakehouse is the system of record; nothing about it lives only on one PC.

> **Status: Preview, engine only.** This release adds the engine that creates, connects to and checks an optimization lakehouse. The Optimize page and its setup screen come in a later release, and none of this has been tried on a live Fabric tenant yet. The plan is in [plan.md](plan.md).

## One lakehouse for the organization

- One lakehouse is enough for a whole organization. You don't need one per developer, project or workspace.
- If you need separate boundaries, such as business units, regulated data or separate environments, use separate optimization lakehouses. Each installation is connected to one at a time and can switch between the ones it knows.
- The default name is `FabricDesktop_Optimization`. It is a separate item from the platform's `LH_Meta`.

## Setting it up

The first person creates the lakehouse: they pick a workspace and a name, and the app does the rest. It creates the lakehouse, creates the tables, records the schema version and default settings, checks it can read and write, and remembers the connection on that PC. Nobody runs SQL or creates tables by hand.

Everyone else connects to the existing lakehouse, and nothing is created again. When the app connects to a lakehouse, the outcome depends on what is already in it:

| The lakehouse holds | What happens |
| --- | --- |
| Optimize's tables at the current version | It connects |
| Nothing | It asks before creating Optimize's tables |
| Other tables, none of Optimize's | It asks, then adds Optimize's tables beside them. The other tables are never read, changed or replaced |
| An unfinished setup or upgrade | It asks, then adds only what is missing. Every row is kept |
| An older Optimize schema | It asks, then upgrades. Upgrades only add tables and columns, and every row is kept |
| A newer Optimize schema | It opens read-only and asks you to update the app |
| A table named `fdopt_...` that isn't Optimize's | It refuses to write and leaves the table alone |

Disconnecting only removes the lakehouse from this PC's list. The lakehouse and its data stay as they are, and connecting again picks up where you left off. The app never deletes an optimization lakehouse.

## Permissions

Optimize signs in as you, the same way as the rest of the app (see [Permissions](../permissions.md)). It needs no new permissions and no Fabric administrator role.

| To | You need |
| --- | --- |
| Create the lakehouse | Contributor or higher on the workspace |
| Add results | Contributor or higher on the workspace |
| Read shared results only | Read all data (ReadAll) on the lakehouse. A workspace Viewer reads lakehouses through the SQL analytics endpoint, not OneLake files, so a Viewer needs the lakehouse shared with Read all data |

The app finds out what you can do by trying it. It reads the schema version, then registers this installation (a row it would write anyway). With read access only, it opens the lakehouse read-only and says so. It doesn't read the workspace's role assignments, which would need admin rights.

## What each installation writes

Each installation writes rows into the shared tables. Every row records which installation wrote it, when, which app version wrote it and which schema version it was written against.

| Field | Example | What it is |
| --- | --- | --- |
| `installation_id` | `3b1f6c2e-...` | A random id made on this PC the first time Optimize needs it. It isn't worked out from you, your account, the PC or your tenant, and it isn't the [usage data](../telemetry.md) install id. **New installation ID** replaces it; rows already written keep the old one |
| `label` | `Build server` | Empty unless someone names the installation |
| `os` | `Windows` | The operating system family, without a version or machine name |
| `app_version` | `0.20.0` | The engine version that wrote the row |
| `created_at`, `registered_at` | `2026-10-18T09:30:00+00:00` | When the row was written |

Analysis runs add the Fabric items they looked at, the problems found and the recommendations made; later releases add measurements and local run records. Those rows hold Fabric workspace and item ids, and the workspace, lakehouse, table and item names they are about. **Names are identifying**: anyone who can read the lakehouse can read them. **SQL text and code are never written.** Only fingerprints of them are stored (the `store_sql_text` setting is `never`).

## Who can see what

Optimize relies on Fabric's own security. It adds no access layer of its own.

- Anyone who can read the optimization lakehouse can read every row in it, including which installation wrote what. OneLake security roles can limit access by table or folder ([OneLake security model](https://learn.microsoft.com/en-us/fabric/onelake/security/data-access-control-model)). Optimize does not rely on row-level filtering for people reading the Delta files directly.
- When groups mustn't see each other's diagnostics, give each group its own optimization lakehouse in a workspace only they can reach.
- Access tokens are never written to the lakehouse.

## How several installations write safely

- **Insert only.** No installation updates or deletes a shared row. A change, such as a recommendation being accepted, is a new row, and the latest row wins.
- **No duplicates.** Every row has a `record_id` worked out from what it records. Rows are written with an insert-only Delta MERGE on it, so a retry or a second sync adds nothing.
- **Bounded batches.** Rows go in batches of at most 5,000, one commit each. A batch that fails after its retries stops the write; the batches before it stay, and writing again adds only what's missing.
- **Bounded retries.** A commit that loses a race with another installation, throttling or a network blip is retried up to five times with growing waits. A permission error is never retried.
- **Race-safe setup.** Two installations setting up the same lakehouse at once end up with one set of tables and one schema version row.
- **Recoverable migrations.** A migration only adds tables or columns, and each step can safely run again. Its version is recorded only after every step succeeded, so a migration that stopped part way finishes the next time.

## Findings and recommendations

Every installation adds what its analysis finds to the shared lakehouse, and everyone connected sees the combined picture.

- **One finding per problem.** A problem is identified by the rule that found it, the Fabric item (by workspace and item id) and what in it the rule looked at, such as a table. When several PCs find the same problem it shows once, with when it was first and last seen, in how many analysis runs and by how many installations.
- **A run counts once it's complete.** An analysis run's own row is written after its findings. If a PC loses its connection part way, nothing from that run shows until it's recorded again, and recording it again adds only the rows that are missing.
- **One recommendation per problem and kind of fix**, opened by whichever installation sees it first. Accepting, applying, dismissing or reopening it is recorded as a change with who made it, when and why, and everyone sees the latest. A dismissed recommendation stays dismissed when the problem is seen again; someone has to reopen it.
- **Allowed changes.** Open can become accepted, applied, dismissed or superseded. Accepted can become applied, dismissed or open again. Applied can become verified or open again. Dismissed and verified can only be reopened. Superseded is final.
- **Only items you can open.** Findings and recommendations about items in workspaces you can't open in Fabric aren't shown to you. This is a courtesy, not security: anyone who can read the lakehouse can read every row ([Who can see what](#who-can-see-what)).

## The tables

All are Delta tables in `dbo` (or directly under Tables in a lakehouse without schemas), named `fdopt_` so they can't collide with yours: `installations`, `analysis_runs`, `artifacts`, `findings`, `recommendations`, `actions`, `measurements`, `cu_usage`, `savings`, `local_execution`, `dependencies`, `rules`, `audit`, `config` and `schema_version`. Columns are in [`sidecar/fabric_desktop/optimize/schema.py`](https://github.com/KratosDataGod/fabric-desktop/blob/main/sidecar/fabric_desktop/optimize/schema.py). You never need to query them yourself.

## For IT admins

Set this in `%ProgramData%\FabricDesktop\policy.json` ([Configure Azure and Fabric](../azure-setup.md#4-push-settings-to-everyone)):

| Key | Effect |
| --- | --- |
| `allow_optimize_lakehouse: false` | The app doesn't create, connect to or write to optimization lakehouses. Analysis on the PC is not affected |

Optimize is separate from usage data. Its rows go to a lakehouse in your own tenant, never to KratosBI, so the usage data switch doesn't affect it.
