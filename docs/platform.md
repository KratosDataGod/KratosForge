# The platform

The platform is what a central data team runs once so every other team can build on it: **one business key system** for every team, **one landing zone** where raw data from every source arrives, one catalog of domains and data products, and one record of runs. Each team then builds its own Bronze from the landing zone, and everything after it. Fabric Desktop by KratosBI creates it for you, in Fabric, from **Platform > Settings**, and the **Platform** page is where the central team runs it and every other team asks for what it needs.

!!! note "Preview"
    The platform is built to Fabric's documented APIs but hasn't been tried on a live tenant yet. Create it in a test tenant or a non-production capacity first. See [What isn't proven yet](#what-isnt-proven-yet).

## What gets created

One **Platform workspace per environment** (Dev, Test, Prod), named `Fabric Desktop Platform - <Environment>` unless you choose another name. Each holds:

| Item | Type | What it's for |
| --- | --- | --- |
| `SQL_Platform` | SQL database | Surrogate keys (`keys.<name>`) and the catalog (`catalog.*`: platform admins, key tables, landing zone sources and their readers, domains, projects, data products and their ports, links, access grants, requests, descriptions, glossary, and an audit trail of every change) |
| `UDF_Platform` | User data functions | The platform's API. Fabric Desktop by KratosBI, notebooks, the headless agent and Power BI buttons all call these functions; nobody connects to the database directly |
| `LH_Landing` | Lakehouse | The one landing zone: raw files from every source system, in `Files/<source>/<entity>/`. Teams read it; they don't copy it |
| `LH_Meta` | Lakehouse | Run history from every project |
| `VL_Platform` | Variable library | The ids of these items, so a notebook can find them |

Domains, the glossary, data products and descriptions don't depend on the environment, so they live in the **Prod** platform. Keys are per environment: Dev keys never mix with Prod keys.

Without a platform in Fabric, the same tables live in a SQLite file on your PC (`%LOCALAPPDATA%\FabricDesktop\platform\`), so one engineer can try domains, products and the catalog before the central team has set anything up. Moving to Fabric later doesn't change how anything works.

## Create it

1. Sign in with an account that can create workspaces on the capacity you'll use.
2. Open **Platform > Settings** and choose **Create** next to Prod (then Test, then Dev, if you want all three).
3. Pick a capacity. A SQL database and user data functions need a Fabric capacity (F2 or larger, or a trial).
4. Each step reports **done** or **failed**. A failed step says what to do; fix it and choose **Finish or repair**. Every step is safe to run again: what exists is kept.
5. You become the platform's first **platform admin**. Add the rest of the central team on **Platform > Settings**.

If the step **Platform tables** fails because `UDF_Platform` has no connection to the database, open `UDF_Platform` in Fabric, choose **Manage connections**, add `SQL_Platform` with the alias `platform`, publish, and choose **Finish or repair**.

## Join it from other PCs

Everyone else chooses **Join existing** next to each environment and picks the Platform workspace. Fabric Desktop by KratosBI finds the items by name.

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

Two things decide it: your **role on the Platform workspace** in Fabric (what you can reach at all), and whether you're a **platform admin** (what the platform lets you change). Nothing new needs to be registered or consented.

| Role on the Platform workspace | Who | Can |
| --- | --- | --- |
| Admin | The central data team | Create and repair the platform, edit the functions, write to `LH_Landing` |
| Viewer, plus **Execute** on `UDF_Platform` | Every engineer | Read the landing zone, get keys, publish products, link to products, describe tables, send requests |

Inside the platform:

| Who | Can change |
| --- | --- |
| **Platform admins** | Domains, business key tables, landing zone sources, shared glossary terms, governance policies, the list of admins, and every request |
| **A domain's owners** (set on the domain) | Their domain's glossary terms, and which governance policies apply to their domain (tightening only) |
| **A data product's owner** | Who else may read their product (access requests) |
| **Everyone** | Catalog text (descriptions, tags, classification), their own products and links, and requests |

The platform checks these itself on every call, as the person Fabric says is calling (a user data function reads the caller from Fabric, never from what the app sends). The app hides buttons you can't use, but that's only a convenience. Whoever sets the platform up is its first admin; a platform with no admin lets the first person who signs in claim it on **Platform > Settings**, and always keeps at least one. Admins are kept in each environment's `SQL_Platform`; adding or removing one on the Platform page changes all of them.

Every change is recorded with who made it and when (**Platform > Audit**).

## The Platform page

The rail's **Platform** page is the central team's home, and the same screens show every other team what the platform offers.

- **Overview:** counts, requests that need you, each environment's platform, landing zone sources (late ones first in red) and recent changes.
- **Landing zone:** each source in `LH_Landing` for the environment picked at the top: its entities, newest files, last arrival, how often it's expected and which projects read it. A source that hasn't arrived within its expected hours (plus a quarter) shows **late**. Folders in the landing zone that no admin registered show **not registered**.
- **Business keys:** the platform's key tables. **New key table** (admins) makes it in Dev, Test and Prod at once, so every environment has the same tables.
- **Domains** and **Glossary:** your organization's domains and shared terms. This is the only place they're created and edited; Data mesh > Domains lists the domains and links here. **Import from Fabric** brings in the domains and subdomains defined in Fabric; it needs a Fabric administrator. See [Domains come from Fabric](data-mesh.md#domains-come-from-fabric).
- **Governance:** the organization's governance policies, their versions, where each applies and why. See [Governance policies](governance.md).
- **Requests:** what domain teams asked for, and what was decided.
- **Audit** and **Settings** (where each environment's platform is, and its admins).

## Requests

What only someone else may give is asked for, not taken:

| Request | Sent from | Decided by | Approving |
| --- | --- | --- | --- |
| **A key table** | Platform > Business keys, or Data mesh > Surrogate keys > Use a key table > Request a new one | A platform admin | Creates the key table in every environment |
| **A source** in the landing zone | Platform > Landing zone or Requests | A platform admin | Registers the source; the central team then lands its files |
| **Access to a data product** open only to its domain, or private | OneLake > Data products > Request access | The product's owner (or a platform admin) | Lets the project link to it. The owner still gives read access in OneLake (a data access role on the Product workspace) |
| **A shortcut** | OneLake > Shortcuts > Request a shortcut | A platform admin, or the owner of the data product it points at | Makes the shortcut, as the person approving, then marks the request approved ([shortcuts.md](shortcuts.md#requests-and-shortcut-needs)) |

Each request records who sent it, why, who decided and a note. A request can be withdrawn while it waits. A product owner can take access away again; existing links stay in the project but no new link is allowed.

## One landing zone

Raw data from every source lands once, in `LH_Landing`, organized by source system: `Files/erp/customers/2026-10-16.csv`. **Only the central team writes there**, with pipelines or copy jobs it owns; nobody lands the same source twice. Platform admins register each source on **Platform > Landing zone** with its owner, how often it should arrive, and the name of the Key Vault secret its connection uses per environment (secrets themselves never go in the platform). A domain team that needs a new source requests it.

Each team builds its own Bronze from it: **Data mesh > New entity > From the landing zone** picks a source and entity, or an entity reads the landing zone with `landing` instead of `files`:

```toml
[source]
landing = "erp/customers/*.csv"
```

In Test and Prod that reads `LH_Landing` in the environment's Platform workspace. In Dev on a PC it reads `%LOCALAPPDATA%\FabricDesktop\platform\landing-dev\Files`, one folder shared by every project on that PC. Give teams **Viewer** on the Platform workspace, or a OneLake data access role on the landing folders they may read; only the central team gets Contributor or above, so only it can write. When a project joins the mesh, the catalog records which sources it reads, so the Landing zone tab shows each source's readers and a source still read can't be removed.

## One business key system

Once an environment's platform is in Fabric, every project's surrogate keys in that environment come from `SQL_Platform`. A new project never gets an `LH_Keys` of its own, so Customer ABC has one key across every team. Keys are still kept apart per environment: Dev keys never mix with Prod keys.

Key tables are the platform's, not a project's. Only platform admins create one (**Platform > Business keys > New key table**, or by approving a request), and it's made in every environment at once. A project picks the ones it uses on **Data mesh > Design > Surrogate keys > Use a key table**. A run that needs a key table the platform doesn't have stops before writing anything and says to request it.

## Moving surrogate keys into the platform

Projects that already issue keys in `LH_Keys` keep them there until you move them. On **Data mesh > Design > Surrogate keys**, choose **Move keys** for Test or Prod. Fabric Desktop by KratosBI:

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

- Calling user data functions with Microsoft's Fabric sign-in (the app uses the `Item.Execute.All` access it already asks for), and that the functions' context names the caller (`executing_user`), which platform admins and the audit trail rely on.
- Creating a SQL database and user data functions through the Fabric items API, and the shape of the functions' definition.
- The T-SQL that issues keys (`OPENJSON`, `IDENTITY_INSERT`, `DBCC CHECKIDENT`) on Fabric SQL database.
- Importing Fabric's domains with Microsoft's Fabric sign-in: whether it gets `Tenant.Read.All` for a Fabric administrator, and the exact shape of `GET /v1/admin/domains?preview=false`.

If you try it, please say how it went in a [bug report or the Ideas forum](ideas.md).

## Related

- [Data mesh](data-mesh.md): domains, data products and links between teams
- [Working across teams](teams.md)
- [Permissions](permissions.md)
