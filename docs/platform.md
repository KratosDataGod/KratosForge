# The platform

The platform is what a central data team runs once so every other team can build on it: **one business key system** for every team, **one landing zone** where raw data from every source arrives, one catalog of domains and data products, and one record of runs. Each team then builds its own Bronze from the landing zone, and everything after it. Fabric Desktop creates it for you, in Fabric, from **Settings > Platform**.

!!! note "Preview"
    The platform is built to Fabric's documented APIs but hasn't been tried on a live tenant yet. Create it in a test tenant or a non-production capacity first. See [What isn't proven yet](#what-isnt-proven-yet).

## What gets created

One **Platform workspace per environment** (Dev, Test, Prod), named `Fabric Desktop Platform - <Environment>` unless you choose another name. Each holds:

| Item | Type | What it's for |
| --- | --- | --- |
| `SQL_Platform` | SQL database | Surrogate keys (`keys.<name>`), source systems (`config.*`) and the catalog (`catalog.*`: domains, projects, data products and their ports, links, descriptions, glossary, and an audit trail of every change) |
| `UDF_Platform` | User data functions | The platform's API. Fabric Desktop, notebooks, the headless agent and Power BI buttons all call these functions; nobody connects to the database directly |
| `LH_Landing` | Lakehouse | The one landing zone: raw files from every source system, in `Files/<source>/<entity>/`. Teams read it; they don't copy it |
| `LH_Meta` | Lakehouse | Run history from every project |
| `VL_Platform` | Variable library | The ids of these items, so a notebook can find them |

Domains, the glossary, data products and descriptions don't depend on the environment, so they live in the **Prod** platform. Keys are per environment: Dev keys never mix with Prod keys.

Without a platform in Fabric, the same tables live in a SQLite file on your PC (`%LOCALAPPDATA%\FabricDesktop\platform\`), so one engineer can try domains, products and the catalog before the central team has set anything up. Moving to Fabric later doesn't change how anything works.

## Create it

1. Sign in with an account that can create workspaces on the capacity you'll use.
2. Open **Settings > Platform** and choose **Create** next to Prod (then Test, then Dev, if you want all three).
3. Pick a capacity. A SQL database and user data functions need a Fabric capacity (F2 or larger, or a trial).
4. Each step reports **done** or **failed**. A failed step says what to do; fix it and choose **Finish or repair**. Every step is safe to run again: what exists is kept.

If the step **Platform tables** fails because `UDF_Platform` has no connection to the database, open `UDF_Platform` in Fabric, choose **Manage connections**, add `SQL_Platform` with the alias `platform`, publish, and choose **Finish or repair**.

## Join it from other PCs

Everyone else chooses **Join existing** next to each environment and picks the Platform workspace. Fabric Desktop finds the items by name.

IT can skip that step for everyone: add the platform to the [organization settings file](azure-setup.md) (`policy.json`), and every PC uses it without anyone choosing:

```json
{
  "platform": {
    "prod": { "workspace_id": "...", "functions_id": "...", "sql_id": "...", "meta_id": "..." },
    "test": { "workspace_id": "...", "functions_id": "..." }
  }
}
```

The ids are in each Platform workspace's `VL_Platform` variable library.

## Who can do what

The platform uses Fabric workspace roles. Nothing new needs to be registered or consented.

| Role on the Platform workspace | Who | Can |
| --- | --- | --- |
| Admin | The central data team | Create and repair the platform, edit the functions |
| Contributor or Member | Domain data owners | Everything Viewer can, and manage items in the workspace |
| Viewer, plus **Execute** on `UDF_Platform` | Every engineer | Get keys, publish products, link to products, describe tables |

Every call runs as the signed-in person, and the audit trail records who did what (**Metadata > Mesh > Glossary** shows recent changes).

## One landing zone

Raw data from every source lands once, in `LH_Landing`, organized by source system: `Files/erp/customers/2026-10-16.csv`. Whoever loads it (a pipeline, a copy job, an upload) writes there; nobody lands the same source twice.

Each team builds its own Bronze from it. An entity reads the landing zone with `landing` instead of `files`:

```toml
[source]
landing = "erp/customers/*.csv"
```

In Test and Prod that reads `LH_Landing` in the environment's Platform workspace. In Dev on a PC it reads `%LOCALAPPDATA%\FabricDesktop\platform\landing-dev\Files`, one folder shared by every project on that PC. Give teams **Viewer** on the Platform workspace, or a OneLake data access role on the landing folders they may read.

## One business key system

Once an environment's platform is in Fabric, every project's surrogate keys in that environment come from `SQL_Platform`. A new project never gets an `LH_Keys` of its own, so Customer ABC has one key across every team. Keys are still kept apart per environment: Dev keys never mix with Prod keys.

## Moving surrogate keys into the platform

Projects that already issue keys in `LH_Keys` keep them there until you move them. On **Metadata > Design > Surrogate keys**, choose **Move keys** for Test or Prod. Fabric Desktop:

1. Copies every key into `SQL_Platform` with the **same number**. A business key that already has a different number in the platform stops the move; nothing is renumbered.
2. Reads the copy back and compares it, row by row, with `LH_Keys`.
3. Only if every key table matches, writes `keys = "platform"` for that environment in `environments.toml`.

`LH_Keys` is only read, never changed. From then on each run asks the platform for keys: new business keys get the next number after the highest one moved.

**Back to LH_Keys** sets `keys = "lakehouse"` instead, but only while the platform hasn't issued keys that `LH_Keys` doesn't have. After that, going back would hand out the same numbers twice, so it's refused.

## How keys are issued at scale

- A run sends its business keys to `issue_keys` in batches of 20,000 and gets every key back in one answer.
- New keys are inserted in business key order in one transaction, with a lock on the rows they'd go in, and each key table has a unique constraint on the business key. Two runs asking for the same new customer at the same time get the same key.
- Keys are never reused or renumbered. `-1` is the Unknown member in every key table.
- The Fabric runner and the headless agent call the same functions, so a run in Fabric, on a PC or on a server issues keys from one place.

## What isn't proven yet

These are built to Microsoft's documentation and tested against SQLite, but haven't run on a live tenant:

- Calling user data functions with Microsoft's Fabric sign-in (the app uses the `Item.Execute.All` access it already asks for).
- Creating a SQL database and user data functions through the Fabric items API, and the shape of the functions' definition.
- The T-SQL that issues keys (`OPENJSON`, `IDENTITY_INSERT`, `DBCC CHECKIDENT`) on Fabric SQL database.

If you try it, please say how it went in a [bug report or the Ideas forum](ideas.md).

## Related

- [Data mesh](data-mesh.md): domains, data products and links between teams
- [Working across teams](teams.md)
- [Permissions](permissions.md)
