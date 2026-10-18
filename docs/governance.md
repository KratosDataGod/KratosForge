# Governance policies

**Preview.** Fabric Desktop by KratosBI keeps your organization's governance standards in one place, the platform's catalog, so they are defined once and every project is checked against the same thing. This page covers the policy registry (GOV-001): writing, publishing and assigning policies, and how a project's policies are worked out. Checking projects against them offline (GOV-002) and the later governance features build on it. For a walk-through with worked examples, and what can and can't be enforced today, see the [governance guide](governance-guide.md).

It complements Microsoft Fabric and Microsoft Purview rather than repeating them: Purview stays the place for sensitivity labels, data maps and tenant-wide classification, and Fabric keeps workspace roles and domains. The registry holds the engineering standards a development team works to on its own PC (naming, schemas, quality, ownership, contracts, deployment readiness) so they can be checked before anything reaches Fabric.

Everything here runs against the platform you already have: `SQL_Platform` through `UDF_Platform` in Fabric, or the SQLite platform on your PC before the platform is in Fabric ([The platform](platform.md)). Working out which policies apply runs on your PC: no Fabric job runs and no OneLake data is read. With the platform in Fabric, reading the policies is one call to `UDF_Platform` (and its `SQL_Platform` database), which uses a little capacity; with the SQLite platform it uses none.

## What a policy is

| Field | What it's for |
| --- | --- |
| **Id** | Unique, lowercase, like `table-names`. Never changes. |
| **Name**, **description** | What people see. |
| **Category** | Naming standards, Schema standards, Data quality requirements, Classification requirements, Ownership and stewardship, Data contract requirements, Deployment readiness. |
| **Owner** | The person or team who answers for the policy. |
| **Severity** | Low, Medium, High or Critical. |
| **Enforcement** | Advisory (shown only), Warn (shown as a warning) or Block. Block is not enforced yet: the app shows it as **Block (not enforced yet)** until checks arrive in GOV-002 and GOV-003, and until then it stops nothing. |
| **Mandatory** | When on, nothing below where the policy is assigned can make it weaker. |
| **Can be assigned at** | Which of enterprise, domain, project and entity it may be assigned to. |
| **Applies to** | Optional: only some layers or environments. |
| **In force** | Optional dates it takes effect and expires. |
| **Exceptions** | Whether people may ask for an exception (with a reason, an approver and an expiry, as in `exceptions.toml`). |
| **Checked by** | The name of the rule that checks it, for the validation that comes with GOV-002. |
| **Parameters** | Named values the rule uses, like `max_length = 60`. Each says which way is **stricter**: higher, lower, more items, fewer items, on, or fixed (can't change under a mandatory assignment). |
| **How to fix** | What a developer does when the policy flags something. |
| **Status** | Draft, Published, Deprecated or Retired. |

## Drafts, versions and status

1. **Draft.** A new policy is draft version 1. A draft can be edited as often as needed, and discarded.
2. **Published.** Publishing turns the draft into a numbered version. A published version is stored once, with the SHA-256 hash of its definition, and never changes. To change a policy, edit it: that starts a new draft (version 2, 3, ...) and the published version stays in force until the new one is published. Every version stays readable. A version whose stored definition no longer matches its hash shows **Changed outside the platform** instead of **Intact**.
3. **Deprecated.** Still applies, with a warning that it's going away. It can be restored.
4. **Retired.** No longer applies anywhere. It keeps all its versions and history, and can't be changed or brought back. Retiring asks for a reason.

Every change (created, edited, published, deprecated, retired, assigned, unassigned) is kept in the policy's **History** with who made it and when, and also in **Platform > Audit**.

## Where a policy applies

A policy is in force only where it is **assigned**. Scopes, from the top:

1. **Enterprise:** every project.
2. **Domain**, then its **subdomain:** projects in that domain (from the catalog, see [Data mesh](data-mesh.md)).
3. **Project:** one metadata project.
4. **Entity:** one table in a project, named `<project>:<entity>`, like `crm:silver.customer`.

An assignment can leave the published settings as they are, or change them for its scope: enforcement, severity, mandatory, a version to use, and parameter values. Several projects can be reached by the same published policy, and the policy's **Where it applies** tab lists every registered project it reaches.

### How the settings combine

Resolution starts at the top and walks down. The first assignment found introduces the policy; each lower one may change its settings.

- **Mandatory locks it from there down.** Once an assignment (or the policy itself) makes a policy mandatory, a lower scope may only make it **stricter**: a higher enforcement or severity, a parameter moved its stricter way, a newer version. Switching it off, lowering enforcement or severity, an older version, or loosening a parameter is refused.
- **Not mandatory:** a lower scope may change anything, including switching it **off** for its scope.
- **Lower scopes may add policies** that aren't assigned above them.
- **Refused or ignored, never silently applied.** A weakening change saved to the platform is refused with the reason. One that comes from a project's own `governance.toml` is ignored, listed as a conflict, and shown in the explanation.
- **Deterministic.** The same policies and assignments always give the same result, in the same order, whatever order they were saved in. Every step is returned: **Explain** shows which scope introduced the policy, what each scope changed, and what was ignored and why.
- A retired policy, one switched off, one not yet in force or expired, and one that doesn't apply to the entity's layer or the environment, are listed as **not in force** with the reason.

### A project's own policies: governance.toml

A metadata project can add policies, or tighten the ones it inherits, in `governance.toml` at its root. It is reviewed through Git like the rest of the project, and a project without the file works exactly as before.

```toml
[[policy]]
id = "table-names"          # a published policy in the registry
enforcement = "block"       # optional: advisory, warn, block, or off (only if not mandatory)
severity = "high"           # optional
[policy.params]
max_length = 40

[[policy]]
id = "owner-required"
entity = "silver.customer"  # optional: only this entity; otherwise the whole project
version = 2                 # optional: a published version to use
```

A setting the file can't have, an entry without an id, or a broken file is reported; it never stops the project from opening or running.

## Who can do what

The platform checks this itself on every call, as the person Fabric says is calling ([Who can do what](platform.md#who-can-do-what)). Hiding buttons in the app is only a convenience.

| Who | Can |
| --- | --- |
| **Platform admins** | Create, edit, publish, deprecate, retire and discard policies; assign them at any scope |
| **A domain's owners** | Assign published policies to their domain and its subdomains, and change or remove those assignments (tightening only under a mandatory policy) |
| **Everyone** | Read every policy, version, assignment and history, and see how policies resolve for any project |

No new Fabric permission or sign-in scope is needed: policies use the same `UDF_Platform` calls as the rest of the platform ([permissions](permissions.md)).

## In the app

**Platform > Governance** lists policies with search, category and status filters and counts by status. Open a policy for:

- **Definition:** the selected version, with every version listed beside it (current, older, draft) and whether each is intact.
- **Where it applies:** its assignments (**Assign**, **Change**, **Remove**) and every registered project it reaches; **Explain** shows how it was worked out for that project, including the open project's `governance.toml`.
- **History:** every change, who made it and when.

Admins see **New policy**, **Edit draft**, **Publish**, **Deprecate** and **Retire**. **Save and publish** saves the draft and publishes it in one step.

## When the platform can't be reached

Checks that depend on the platform fail closed: when a rule can't be read, Fabric Desktop by KratosBI stops the action and says why, rather than acting as if there were no rule. Today that covers the [shortcut rules](shortcuts.md#shortcut-rules), and who-can-read lists, which say **Not complete** when a lakehouse's data access roles can't be read. Policy checks (GOV-002 onward) will follow the same rule.

## Storage

Policies are four tables in the platform's catalog, next to domains and the glossary (no second database):

| Table | Holds |
| --- | --- |
| `catalog.policy` | One row per policy: its latest name, category, owner, status, current and draft version |
| `catalog.policy_version` | Every version: draft or published, its definition (JSON), SHA-256 hash, note, who and when |
| `catalog.policy_assignment` | Where each policy is assigned, and the settings it has there |
| `catalog.policy_event` | Each policy's history |

The platform's schema version goes from 3 to 4. The tables are created by **Finish setup** (as every platform table is), and also on the first policy call to a platform that doesn't have them yet, so existing platforms and projects keep working without a migration step. A policy's definition is stored as JSON, so new fields can be added later without changing the tables.

For Fabric, `UDF_Platform` gains one function per policy operation. Re-run **Platform > Settings > Finish or repair** to update the function app.

## For developers

The rules live in `sidecar/fabric_desktop/central/policy.py`, standard library only, next to `core.py`; `udf.py` builds the function app from both. `resolve(snapshot, chain, local, context, today)` is a pure function over plain data (`policy_snapshot` returns that data), so offline validation can run it from a downloaded copy.

The desktop calls these engine methods (JSON-RPC, see [architecture](architecture.md)): `governance_overview`, `governance_policy`, `governance_version`, `governance_create`, `governance_save_draft`, `governance_discard_draft`, `governance_publish`, `governance_set_status`, `governance_assign`, `governance_unassign`, `governance_applicability`, `governance_resolve`.

## What isn't built yet

- Checking a project against its policies (GOV-002, offline policy validation) and blocking promote or deployment on a **Block** policy (later GOV items). Today a policy's **Checked by** rule is a name only.
- Signed policy packages for offline use (GOV-002).
- Exception requests against a registry policy. Project exceptions in `exceptions.toml` are unchanged.
- A separate approver for publishing (the same admin can write and publish).
- Not yet tried against `SQL_Platform` in a live Fabric tenant (hence Preview).
