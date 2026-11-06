# Optimize: security and retention

This page is for whoever decides who may read or write Optimize's central lakehouse. It covers what each `fdopt_` table holds, what in it is identifying, who can read and write it, and what is and isn't enforced. [Optimize and the central lakehouse](README.md) covers setup, permissions and the other table families.

## What is verified, and what isn't

Everything on this page is tested against lakehouse folders on a PC and a fake Fabric API. **None of it has yet been tried on a live Fabric tenant**, including the role changes, the Read all data path and OneLake security. The access rules below are Fabric's own, as Microsoft documents them. Optimize adds no access layer of its own.

## Who can do what

| To | You need | Notes |
| --- | --- | --- |
| Read every `fdopt_` row | Read all data (ReadAll) on the lakehouse, or any workspace role that includes it | A reporting role needs only this. A workspace Viewer reads through the SQL analytics endpoint, not OneLake files, so share the lakehouse with Read all data instead |
| Add rows | Contributor or higher on the workspace | Every writer can add rows of every `fdopt_` table |
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
| `actions`, `measurements`, `cu_usage`, `savings`, `dependencies`, `rules` | Not yet | Created empty, for later work in the [plan](plan.md) | — |

These are never written: SQL text or code (only fingerprints of them), table contents, access tokens, email addresses, and X-ray's access findings, which name people. X-ray's cost findings aren't written either, because they are estimates for one PC.

## Things readers and writers should know

- **Rows aren't signed.** `installation_id` says which installation wrote a row, but it isn't proof. Anyone with Contributor on the workspace can write any row, including one that looks like another installation's. Treat the tables as shared diagnostics, not as an audit trail you would defend in a dispute. Give Contributor only to people you would trust with the workspace anyway.
- **Insert only.** The app never updates or deletes a row. A change of state is a new row, and the latest one wins. A Contributor using other tools can still change the Delta tables directly, and OneLake keeps the history.
- **Showing only workspaces you can open is a courtesy.** The app hides findings about workspaces you can't open in Fabric, but anyone who can read the files can read every row. When groups mustn't see each other's diagnostics, give each group its own optimization lakehouse in a workspace only they can reach.
- **Results waiting on a PC.** When the lakehouse can't be reached, results wait in a SQLite file in the app's data folder on that PC, with errors stored without secrets ([When the lakehouse can't be reached](README.md#when-the-lakehouse-cant-be-reached)). They are as readable as anything else in the person's profile.

## Retention

`retention_days` (default 365) is stored in `fdopt_config`, but **nothing enforces it yet**: rows are kept until someone deletes them. Deleting shared rows can't be undone, so retention is planned as an administrator action, never automatic. It will:

- run a dry run first that lists what would go;
- touch only `fdopt_` tables, never another family's;
- record what it did in `fdopt_audit`.

It needs a mockup and a go before it's built ([plan](plan.md), OPT-008). Until then, an administrator who must remove rows deletes them with their own tools, for example a notebook running `DELETE` on the Delta table, followed by `VACUUM` once the history is no longer needed.
