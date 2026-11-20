# Optimize: security and retention

This page is for whoever decides who may read or write Optimize's central lakehouse. It covers what each `fdopt_` table holds, what in it is identifying, who can read and write it, and what is and isn't enforced. [Optimize and the central lakehouse](README.md) covers setup, permissions and the other table families.

## What is verified, and what isn't

Everything on this page is tested against lakehouse folders on a PC and a fake Fabric API. **None of it has yet been tried on a live Fabric tenant**, including the role changes, the Read all data path and OneLake security. The access rules below are Fabric's own, as Microsoft documents them. Optimize adds no access layer of its own.

## Who can do what

| To | You need | Notes |
| --- | --- | --- |
| Read every `fdopt_` row | Read all data (ReadAll) on the lakehouse, or any workspace role that includes it | A reporting role needs only this. A workspace Viewer reads through the SQL analytics endpoint, not OneLake files, so share the lakehouse with Read all data instead |
| Add rows | Contributor or higher on the workspace | Every writer can add rows of every `fdopt_` table. Every writer can also read every row ([Writers can read everything](#writers-can-read-everything)) |
| Create the lakehouse, or upgrade its tables | Contributor or higher on the workspace | Upgrades only add tables and nullable columns |
| Keep a family's tables from some readers | The right to manage the lakehouse's OneLake roles | See [Tables only some people may read](README.md#tables-only-some-people-may-read). `fdopt_` tables stay in `dbo`, readable by everyone with read access |

An organization's policy can turn all of this off: with `allow_optimize_lakehouse: false` the app doesn't create, connect to or write to the central lakehouse ([For IT admins](README.md#for-it-admins)).

## What each table holds

Every row also carries its provenance: `record_id`, `installation_id` (a random id made on the PC), `created_at`, `app_version` and `schema_version`. **Names are identifying.** Workspace, lakehouse, table, notebook and project names say what an organization works on, and anyone who can read the lakehouse can read all of them, whatever they can open in Fabric.

| Table | Written today | Holds | Identifying |
| --- | --- | --- | --- |
| `installations` | Yes | One row per installation: an optional label, the OS family, and when it registered | The label, if someone sets one |
| `analysis_runs` | Yes | When an analysis ran, its scope and status, and how many items and findings it saw | No |
| `artifacts` | Yes | Fabric items an analysis looked at, by workspace and item id, with their display names | Item and workspace ids and names |
| `findings` | Yes | One row each time a rule finds a problem: rule, item, target (such as `schema.table`), severity, title, detail and evidence counts | Item ids, table names, and names in titles and details |
| `recommendations` | Yes | Recommendation events: opened, accepted, applied, dismissed with the reason someone typed, verified | Dismiss reasons are free text. The app removes anything credential-shaped, but people should still not type names in them |
| `local_execution` | Yes, when someone shares runs | Runs on a PC: when, how long, rows written, result, the table, project or notebook name, and CPU time and peak memory where Prove measured them | Table, project and notebook names |
| `audit` | Yes | Setup and role changes, and recommendation state changes: what, on what, the outcome | The targets named |
| `config` | Yes | Settings shared by everyone on the lakehouse, such as `store_sql_text` = `never` and `retention_days` | No |
| `schema_version` | Yes | Which upgrades have run | No |
| `measurements` | Yes, on each sync | What each write to the lakehouse took on a PC: seconds, rows and commits, as Measured figures ([What syncing costs](README.md#what-syncing-costs)) | No |
| `actions`, `cu_usage`, `dependencies`, `rules` | Not yet | Created empty, for later work in the [plan](plan.md) | — |
| `savings` | Not yet | Schema version 2 makes it the savings ledger: append-only events per run and attribution layer, with pricing and baseline versions and the event a correction corrects. Nothing is written until a measured Fabric baseline exists | Workspace, capacity and artifact ids |
| `pricing` | Not yet (version 2) | Pricing scenarios: SKU, region, currency, rate per CU hour, pricing model, effective dates, source and confidence. An assumption, never a charge | No |
| `optimization_link` | Not yet (version 2) | Which artifacts, workspaces and capacities an optimization affects | Workspace, capacity and artifact ids |
| `baseline` | Not yet (version 2) | A workload's versioned baseline: definition fingerprint, measurement window, how many runs and which measurements | The workload key (table, project or notebook names) |
| `validation` | Not yet (version 2) | An optimization's before and after comparison, whether outputs matched, and the result with its reason | Reasons are free text; when they are written they go through secret removal, as dismiss reasons do |
| `attribution` | Not yet (version 2) | One row per layer of a run's savings breakdown (optimization, then local run), never more than the baseline | The workload key |
| `sync` | Not yet (version 2) | Each sync of an installation: kind, rows, commits, seconds and outcome | No |

Schema version 2 also adds empty columns to `local_execution` (workspace and capacity ids, a fingerprint of the definition, bytes in and out, reads, writes and transfer time), `measurements` (comparison id, baseline version) and `recommendations` (optimization id, category, fingerprints of the original and optimized definitions, rollback note, event version). The rollback note is free text; when it is written it goes through secret removal. An installation still on version 1 opens an upgraded lakehouse read-only until it is updated.

**What is filled today.** The version 2 tables and columns are created by the upgrade (`optimize/schema.py`, `TABLES_V2`), but nothing writes them yet. Today's writers, `optimize/shared.py` and `optimize/local_runs.py`, fill only the version 1 columns, so the new columns stay empty and the six new tables have no rows. The table above says "Not yet" for the same reason. Each story in the [savings plan](../product/enterprise-savings-intelligence-plan.md) that starts writing one of them updates this page.

### Who did something: user and tenant ids (planned)

Schema version 2 has **no user or tenant column**. No row today says which person ran, shared or changed something, or which tenant it belongs to. Rows say only which installation wrote them, and `installation_id` is a random id that isn't worked out from the person, the account or the PC (`optimize/installation.py`).

The plan adds them in story ESI-102. It would read the signed-in person's Microsoft Entra object id and tenant id from the sign-in token, never from a setting someone types, and put them on the rows that person causes. That needs Chris's decision D3 and a later additive migration. Until both happen, nothing collects or stores these ids.

When they arrive, treat both as personal data:

- **The object id identifies one person.** It is a GUID, not a name, but anyone who can look it up in the tenant's directory can tell who it is. Rows with it say what that person ran, when, on which workspaces, and what they changed.
- **The tenant id identifies the organization.** Joined with the object id, it identifies the person outside the tenant too.
- **No email address, UPN or display name will be stored** on these rows (D3). Display names are planned only as something an organization turns on for contributor recognition (D5), in a table of their own. That isn't built either.

Anyone who can read the lakehouse will be able to read these ids, like everything else in it. Decide who may read the lakehouse with that in mind before the ids are turned on.

These are never written: SQL text or code (only fingerprints of them), table contents, access tokens, email addresses, and X-ray's access findings, which name people. X-ray's cost findings aren't written either, because they are estimates for one PC.

## Things readers and writers should know

### Writers can read everything

Each installation writes its rows straight into the lakehouse's Delta tables in OneLake, signed in as the person using it (`optimize/shared.py`, `optimize/lakehouse.py`). The plan calls this mode A, and it is the only way Optimize writes today. Writing that way needs Contributor or higher on the workspace, and that role can also read every table in the lakehouse. OneLake has no row-level security for writes, and OneLake security roles don't limit what a workspace Contributor can read, as Microsoft documents it. So:

- **Anyone who can add rows can read every row**, from every installation, about every workspace, lakehouse, table and notebook named in them. That includes the user and tenant ids once they are added.
- **You can't give someone the right to contribute results without the right to read everyone's.** Give Contributor on the optimization lakehouse's workspace only to people you would let see every row in it, now and as more columns are filled.
- **People who only need reports don't need Contributor.** Give them Read all data on the lakehouse. A table family that names people can be kept from some readers with OneLake security roles ([Tables only some people may read](README.md#tables-only-some-people-may-read)), but those roles limit readers, not Contributors.
- **Groups that mustn't see each other's rows need separate lakehouses**, each in a workspace only that group can reach.

Separating writers from readers needs a different way to write. The plan's mode C would have desktops send rows to a collector function that writes them, so writers would need only the right to run it. Mode C isn't built, whether a function can write these tables isn't proven, and building it needs Chris's go ([plan, section 4.2](../product/enterprise-savings-intelligence-plan.md#42-ingestion-why-direct-merge-stays-the-default)).

### Other things to know

- **Rows aren't signed.** `installation_id` says which installation wrote a row, but it isn't proof. Anyone with Contributor on the workspace can write any row, including one that looks like another installation's. Treat the tables as shared diagnostics, not as an audit trail you would defend in a dispute. Give Contributor only to people you would trust with the workspace anyway.
- **Insert only.** The app never updates or deletes a row. A change of state is a new row, and the latest one wins. A Contributor using other tools can still change the Delta tables directly, and OneLake keeps the history.
- **Showing only workspaces you can open is a courtesy.** The app hides findings about workspaces you can't open in Fabric, but anyone who can read the files can read every row. When groups mustn't see each other's diagnostics, give each group its own optimization lakehouse in a workspace only they can reach.
- **Results waiting on a PC.** When the lakehouse can't be reached, results wait in a SQLite file in the app's data folder on that PC, with errors stored without secrets ([When the lakehouse can't be reached](README.md#when-the-lakehouse-cant-be-reached)). They are as readable as anything else in the person's profile. How long they stay is under [Retention](#retention).

## Retention

### On the PC

Results that couldn't be sent wait in `optimize\outbox.sqlite` in the app's data folder (`optimize/outbox.py`). Each item holds the same rows it would have written to the lakehouse, with the same names and ids, as plain JSON. The app doesn't encrypt the file; it is protected only as the person's profile is.

- **Sent items are deleted** from the file as soon as the lakehouse takes them.
- **At most 200 items and 30 days per lakehouse.** Past either, the oldest are deleted and counted as dropped. The limits are checked when Optimize next queues or sends something for that lakehouse, so an item older than 30 days can stay in the file until then, for example for a lakehouse this PC no longer uses.
- **An item the lakehouse refuses as it is** is deleted and counted as dropped.
- **Disconnecting keeps what waits** for that lakehouse, to be sent if the PC connects again.
- **Per lakehouse, the file also keeps** when it last sent, how many items were dropped, and the last error, with secrets removed and cut to 500 characters. These have no time limit. The last error is cleared by the next send that works.
- **A damaged file is moved aside** as `outbox.sqlite.corrupt-<time>` and a new one starts. The app never sends from the moved file. It keeps the five newest moved copies and deletes older ones the next time it moves one aside (`jsonfile.py`). The copies have no time limit; to remove them sooner, delete them by hand.

Not built: there is no button or call that empties the outbox. `Outbox.discard` exists in the code, but nothing in the app calls it. To clear what waits, close the app and delete the file.

### In the lakehouse

Rows are insert-only. No installation updates or deletes a row (`optimize/shared.py`), and a correction or change of state is a new row. There is **no automatic deletion** of any `fdopt_` row today, whatever its age, including the version 2 tables once they are written.

`retention_days` (default 365) is stored in `fdopt_config` (`optimize/schema.py`), but **nothing enforces it yet**: rows are kept until someone deletes them. Deleting shared rows can't be undone, so retention is planned as an administrator action, never automatic. It will:

- run a dry run first that lists what would go;
- touch only `fdopt_` tables, never another family's;
- record what it did in `fdopt_audit`.

It needs a mockup and a go before it's built ([plan](plan.md), OPT-008). Until then, an administrator who must remove rows deletes them with their own tools, for example a notebook running `DELETE` on the Delta table, followed by `VACUUM` once the history is no longer needed.

Nothing in the app removes one person's or one installation's rows either. **New installation ID** changes the id on new rows only; rows already written keep the old one. Once user ids are stored (planned, above), a request to remove a person's data will also need an administrator's own tools. A `DELETE` alone leaves the old rows in the table's Delta history until `VACUUM` removes the files that hold them.
