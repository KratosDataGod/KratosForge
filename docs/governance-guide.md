# Governance: how it works and what can be enforced

**Preview.** This guide walks through governance in KratosForge from start to finish, with three worked examples, and says plainly what can and can't be enforced. The reference for every field and rule is [Governance policies](governance.md).

## The short version

- A **policy** is a standard your organization writes once, like "table names are snake_case and at most 60 characters".
- You **publish** it, which freezes that version, and **assign** it where it applies: the whole enterprise, a domain, a project or one table.
- Each project works out which policies apply to it, and **Explain** shows why.
- **Today nothing is enforced by a policy.** Policies are recorded, versioned, assigned and explained, but no check runs against them yet. Checks come with GOV-002 (checking a project on your PC) and GOV-003 (blocking promote and deploy). Until then, a policy set to **Block** shows as **Block (not enforced yet)**.

Some standards are already enforced today by the app's own gates, without a policy. The [enforcement table](#what-can-be-enforced) below lists them, so you know which standards already hold.

## From draft to result

Each step below is saved in the platform's catalog: the policy and its versions in `catalog.policy` and `catalog.policy_version`, assignments in `catalog.policy_assignment`, and every create, publish and assign as a row in `catalog.policy_event`.

1. **Write it.** Platform > Governance > **New policy**. Give it an id, a name, a category, an owner, a severity, an enforcement (Advisory, Warn or Block), the scopes it may be assigned at, and **How to fix** text for developers. It starts as draft version 1.
2. **Publish it.** **Publish** turns the draft into version 1. A published version never changes; editing starts version 2 as a new draft.
3. **Assign it.** On the policy's **Where it applies** tab, choose **Assign** and pick a scope. An assignment can tighten the policy for that scope. If the policy is **Mandatory**, scopes below may only make it stricter.
4. **See the result.** **Where it applies** lists every project the policy reaches. **Explain** next to a project shows each step: which scope introduced it, what each scope changed, and anything that was ignored and why.
5. **Tighten it in a project.** A project can tighten or add policies in `governance.toml`, reviewed in Git like any other project file.
6. **(Coming with GOV-002 and GOV-003)** The policy's **Checked by** rule runs against the project. An Advisory finding is shown, a Warn finding is shown as a warning, and a Block finding stops promote or deploy until it's fixed or an exception is approved.

## Worked examples

The ids and **Checked by** rule names below are illustrative. Use your own. The rule names become real check names with GOV-002.

### 1. A naming policy

Your standard: Silver and Gold table names are lowercase snake_case, at most 60 characters.

| Field | Value |
| --- | --- |
| Id | `table-names` |
| Category | Naming standards |
| Enforcement | Warn |
| Mandatory | On |
| Can be assigned at | Enterprise, domain, project |
| Applies to | Layers: silver, gold |
| Checked by | `naming.table_name` |
| Parameters | `max_length = 60` (stricter: lower), `pattern = "snake_case"` (fixed) |
| How to fix | Rename the entity file and its `name`; the save gate shows what reads it |

Publish it and assign it at **Enterprise**. The Finance domain owners then assign it to Finance with `max_length = 40`. That's allowed because 40 is stricter. If a Finance project's `governance.toml` sets `max_length = 80`, that change is ignored, and Explain lists it as a conflict.

### 2. A classification policy

Your standard: every Gold table has a classification set, and tables classified Restricted must not be in a Public data product.

| Field | Value |
| --- | --- |
| Id | `gold-classified` |
| Category | Classification requirements |
| Enforcement | Block |
| Mandatory | On |
| Applies to | Layers: gold |
| Checked by | `classification.required` |
| How to fix | Set the classification on the entity's About card (Data mesh > entity > About) |

Classification here is the catalog's own (Public, Internal, Confidential, Restricted). It is not a Microsoft Purview sensitivity label. Labels, and what Fabric does with them (DLP, downstream inheritance), stay in Purview.

### 3. An ownership policy

Your standard: every entity with custom code has an owner, and every data product has an owner and a steward.

| Field | Value |
| --- | --- |
| Id | `owner-required` |
| Category | Ownership and stewardship |
| Enforcement | Block |
| Mandatory | On (a domain may keep it as is but not loosen it) |
| Checked by | `ownership.entity_owner`, `ownership.product_owner` |
| Exceptions | Allowed, with an approver and an expiry |

The save gate already requires an owner for custom code (see the table below), so for that part the policy records the standard the app already holds. The product part becomes enforceable with GOV-002.

## What can be enforced

There are three kinds of enforcement, and they differ in what each can stop:

- **KratosForge blocks:** the app refuses its own step (save, run, promote, deploy, shortcut, request approval). This holds for anything done through KratosForge. It can't stop someone working in the Fabric portal directly.
- **Detect and warn:** the app reads what's in Fabric and reports what breaks the standard, including items made outside the app. It can't prevent them.
- **Fabric, Purview or Entra only:** only the Microsoft service can enforce it, for everyone. KratosForge can show the state and link to the right place.

"Today" means what runs in the current build. "With policies" means what GOV-002 and GOV-003 add.

| Category | KratosForge blocks | Detect and warn | Fabric, Purview or Entra only |
| --- | --- | --- | --- |
| **Naming** | Today: entity and key table names must be valid when saved. With policies: naming rules on entities, layers and products at save and promote | With policies: names of items made in Fabric outside the app | Workspace and item names in the Fabric portal |
| **Schema** | Today: the save gate checks every column exists upstream and shows what a rename or delete breaks; drift settings per layer decide whether a new column is accepted | Today: schema drift is recorded in `meta.schema_history` and shown for review | Table schemas changed directly in a lakehouse |
| **Data quality** | Today: checks run before every write and can stop it (`on_fail = "stop"`) or quarantine rows; promote needs a green last run and passing tests | Today: `warn` checks and Table health findings | Materialized lake view constraints, and Microsoft Purview Data Quality where you use it. Neither reads these policies |
| **Classification** | With policies: block promote when a required classification is missing | With policies: tables without classification, and Restricted tables in open products | Sensitivity labels, label inheritance and DLP (Purview) |
| **Ownership** | Today: custom code needs an owner and a test at save. With policies: owners on entities, products and domains | With policies: items and domains without owners | Workspace roles and item ownership (Fabric) |
| **Data contracts** | With policies: block a promote that breaks a published product's port (a removed or renamed column) | Today: product deprecations carry a date, and the catalog lists every consumer. With policies: consumers reading outside a link | Access to the tables themselves (OneLake security roles, workspace roles) |
| **Deployment readiness** | Today: the promote gate (save gate, tests, green last run) and, when set, a separate promoter for Prod; shortcut rules on shortcuts made in the app | Today: Health flags shortcuts that break the rules when made outside the app | Who may write to a workspace (Fabric workspace roles); tenant settings (Fabric admin) |

Two things no KratosForge setting can do: stop a workspace Contributor from making changes directly in the Fabric portal, and replace Fabric or Entra permissions. To make sure only release managers change Prod, give only them Contributor or higher on the Prod workspaces.

## Exceptions

When a standard genuinely doesn't fit, an exception records why, who approved it and until when. Today exceptions live in a project's `exceptions.toml` and apply to the save gate's rules (see [the metadata guide](metadata-guide.md#6-quality-checks-quarantine-and-the-four-gates)). Exception requests against registry policies, approved on Platform > Requests, come with the GOV items that follow GOV-002.

## When the platform can't be reached

Checks that depend on the platform fail closed. When a rule can't be read, KratosForge stops the step and says why instead of acting as if there were no rule (see [Governance policies](governance.md)).

## Teams notices for requests and shortcuts

With a platform Teams route set (**Settings > Notifications**), KratosForge posts:

| When | Kind | For |
| --- | --- | --- |
| A request is made (a key table, a source, access to a data product, a shortcut) | `request.waiting` | Platform admins, and the data product's owners when it names one |
| It is approved or declined | `request.decided` | Whoever asked |
| It is withdrawn | `request.withdrawn` | Platform admins and the product's owners |
| A health check on **Shortcuts > Health** finds a shortcut broken, or fixed again | `shortcut.broken`, `shortcut.fixed` | Platform admins |

- **Only what the channel may read.** A message names the request or shortcut and where to open it in the app. A request's reason and detail stay in the app. People are named by UPN. Anything that looks like a secret, a webhook address among them, is masked before it's stored or posted.
- **Once each.** A request waiting, or a shortcut that stays broken, is posted once. A fix is posted only after its break was.
- **Never in the way.** The request or check is made first. If the notice can't be sent, the change still stands and the app says the notice wasn't sent. Messages Teams doesn't take wait in the outbox and go out later.
