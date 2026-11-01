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
- Reading the platform writes no rows, except the first admin's claim on an empty platform (`claim admin`). A refused change writes none either.
