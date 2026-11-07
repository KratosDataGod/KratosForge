# The platform's audit trail

What the platform records each time someone asks for something, decides it, or takes access away, and how to pull one request's whole history. It's written for access reviews and enterprise auditors. The app's own per-PC log of runs and changes is a different trail, described in [User guide: audit trail](user-guide.md#audit-trail).

## Where it is

Every change made through the platform adds a row to `catalog.audit` in the platform's database: `SQL_Platform` in each environment's Platform workspace in Fabric ([The platform](platform.md)), or `%LOCALAPPDATA%\FabricDesktop\platform\platform-<env>.sqlite` for a platform kept on one PC. **Platform > Govern > Audit** shows the newest 200 rows. For anything older, query the table directly.

| Column | What it holds |
| --- | --- |
| `id` | Increasing number, in the order rows were written |
| `at` | When (UTC, ISO 8601) |
| `by_user` | Who. In Fabric, it's the caller that Fabric's user data function context names (`executing_user`), not a name the app sends |
| `action` | What was done, listed below |
| `target` | What it was done to: a request's target, a product id, a domain, a source, a person |
| `detail` | JSON with the ids and facts for that action, listed below |

Rows are only ever added: no platform function changes or deletes one, and a test fails the build if one does. Who can change the table directly is decided by who can write to `SQL_Platform` in Fabric, so keep write access to the Platform workspace for the central team.

## A request, from sent to closed

Each row about a request carries its id in `detail.request`, so one id finds them all.

| When | `action` | `detail` |
| --- | --- | --- |
| Someone sends it | `request <kind>` (for example `request source`) | `request`, `project`. The reason stays on the request row, not on the audit row |
| Approving makes something in Fabric, one step at a time | `made for <kind> request` | `request`, and `made`: the ids of what that step made (a workspace, a role and whether it was given, raised or kept, a Copy job and its schedule, a domain, a shortcut) |
| It's approved | `approved <kind>` | `request`, `project` |
| It's declined | `declined <kind>` | `request`, `project`, and `left`: the ids of anything approving had already made, which stays in Fabric |
| It's withdrawn | `withdrew <kind>` | `request`, `project`, and `left` as for a decline |

The request row itself (`catalog.request`) keeps who sent it and when, the reason, the status, who decided it and when, and the decision's note. Once it's decided or withdrawn, nothing changes it.

## Access to a data product

| When | `action` | `target` | `detail` |
| --- | --- | --- | --- |
| An access request is approved | `approved access` | the product | `request`, `project`. The grant (`catalog.grant`) records the request, who granted it and when |
| The owner confirms it's still needed | `confirm access` | the product | `projects` |
| Access is taken away | `revoke access` | the product | `project`, and from the grant it removed: `request`, `granted_by`, `granted_at` |

So a revoke's `detail.request` leads back to the request that gave the access.

## Platform admins and deletes

| `action` | `target` |
| --- | --- |
| `claim admin` (the first admin of an empty platform), `add admin`, `remove admin` | the person |
| `delete domain`, `delete source`, `delete landing zone` | what was deleted. Each is refused while something still uses it, and the refusal writes no row |

Every other change writes a row too, each with its own `action`: `save domain`, `save source`, `save landing zone`, `save term`, `save shortcut rules`, `save Entra group naming`, `link Entra group` and `unlink Entra group`, `publish product`, `add product item` and `remove product item`, `add product term` and `remove product term`, `change project` and `project member`. Keys, shortcuts and the landing zone write theirs as well (for example `stage keys`, `keys live`, `shortcut scan`, `switch landing reads`), and so do actions taken from a workload page (`workload <action> <status>`). Filter on the exact names, not on a prefix. Governance policies, exceptions and templates write their own rows (`policy …`, `ended exception`, `policy template …`), described in [Governance](governance.md).

## Refused changes

A change the platform refuses (the caller isn't an admin, an owner or a member, or a check on what was asked fails) leaves a `refused <operation>` row, for example `refused delete_domain`, whoever called: the app, a script, or another tool calling `UDF_Platform` directly. The change itself is rolled back first, and the row is written on its own.

| Column | What it holds |
| --- | --- |
| `by_user` | The caller as the platform resolved it (Fabric's `executing_user` in Fabric, the signed-in person on one PC), never a name sent with the call |
| `action` | `refused` and the operation's fixed name. A change sent through `call_once` is named by the operation it carried; a name the platform doesn't know is recorded as `refused call_once` |
| `target` | Empty |
| `detail` | `count`: how many refusals of that operation, by that caller, the row stands for |

The row never holds the call's arguments or the refusal's message, which can quote them. Refused reads leave no row. Each row is a write the capacity pays for, so one caller and operation get at most one row a minute (later refusals in that minute are counted into the next row the same process writes), one caller gets at most 30 refused rows an hour, and the platform at most 300. The first refusal past either cap in an hour writes one `refusals not recorded` row (`by_user` is the caller for their own cap, empty for the platform's; `detail` has `cap`: `person` or `platform`, and `count`), and later ones that hour aren't recorded. They're still refused. A row that can't be written never changes the refusal the caller gets.

## Pulling one request's trail

In the Fabric SQL database (T-SQL):

```sql
SELECT at, by_user, action, target, detail
FROM catalog.audit
WHERE JSON_VALUE(detail, '$.request') = '<request id>'
ORDER BY id;
```

For a platform kept on one PC (SQLite):

```sql
SELECT at, by_user, action, target, detail
FROM catalog__audit
WHERE json_extract(detail, '$.request') = '<request id>'
ORDER BY id;
```

To find a request's id, look it up in `catalog.request` by `target`, `requested_by` and `requested_at`, or read it from any audit row about the request.

## Limits

- Rows written before this release keep their older shape: a sent row without `request`, and a revoke without the grant's fields. Join those on `target` and time.
- The platform's trail is append-only through its functions but isn't tamper-evident (no hash chain, unlike the app's per-PC log): its integrity rests on who can write to `SQL_Platform`, so keep write access to the Platform workspace for the central team.
- Reading the platform writes no rows, except the first admin's claim on an empty platform (`claim admin`). A refused read writes none either. A refused change writes its `refused <operation>` row ([Refused changes](#refused-changes)).
- `refused` covers every change the platform turns down: missing rights, and also a check on what was asked (a name in the wrong form, a missing value). Tell them apart by who and what was attempted, not by the row.
- Past a cap, refusals that hour aren't recorded one by one: the `refusals not recorded` row marks when the gap began. Each caller's own cap (30 an hour) keeps one caller from using up the platform's 300.
- The rate checks read `catalog.audit` by `action` and `at`, which have no index yet. An index on (`action`, `at`) is planned.
