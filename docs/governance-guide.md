# Governance: how it works and what can be enforced

**Preview.** This guide walks through governance in KratosForge from start to finish, with three worked examples, and says plainly what can and can't be enforced. The reference for every field and rule is [Governance policies](governance.md).

## The short version

- A **policy** is a standard your organization writes once, like "table names are snake_case and at most 60 characters".
- You **publish** it, which freezes that version, and **assign** it where it applies: the whole enterprise, a domain, a project or one table.
- Each project works out which policies apply to it, and **Explain** shows why.
- **Policies warn; nothing blocks yet.** **Check now** (Preview) checks the open project against its policies on your PC and lists what to look at, but it stops nothing. Blocking promote and deploy comes with GOV-003. Until then, a policy set to **Block** shows as **Block (not enforced yet)**.
- **It works offline.** Each Check now that reaches the platform keeps a hash-checked **policy package** of the project's policies on your PC; without the platform, Check now uses it and says how old it is. `fabric-desktop governance check` runs the same checks from the command line for CI, and fails on an Error finding no exception covers.
- **Start from a template.** Four built-in templates (table names, product owners, sensitive column labels, a data contract) and your organization's own make a policy in a few clicks. See [a worked template](#4-from-a-template-a-data-contract).

Some standards are already enforced today by the app's own gates, without a policy. The [enforcement table](#what-can-be-enforced) below lists them, so you know which standards already hold.

## From draft to result

Each step below is saved in the platform's catalog: the policy and its versions in `catalog.policy` and `catalog.policy_version`, assignments in `catalog.policy_assignment`, and every create, publish and assign as a row in `catalog.policy_event`.

1. **Write it.** Platform > Governance > **New policy**. Give it an id, a name, a category, an owner, a severity, an enforcement (Advisory, Warn or Block), the scopes it may be assigned at, and **How to fix** text for developers. It starts as draft version 1.
2. **Publish it.** **Publish** turns the draft into version 1. A published version never changes; editing starts version 2 as a new draft.
3. **Assign it.** On the policy's **Where it applies** tab, choose **Assign** and pick a scope. An assignment can tighten the policy for that scope. If the policy is **Mandatory**, scopes below may only make it stricter.
4. **See the result.** **Where it applies** lists every project the policy reaches. **Explain** next to a project shows each step: which scope introduced it, what each scope changed, and anything that was ignored and why.
5. **Tighten it in a project.** A project can tighten or add policies in `governance.toml`, reviewed in Git like any other project file.
6. **Check the project.** Platform > Governance > **Check this project** > **Check now** runs each policy's **Checked by** rules on your PC and lists what to look at, with a fix or a link to where to fix it. It's warn only. Without the platform it uses the project's latest policy package on your PC, and says which and how old ([offline policy packages](governance.md#offline-policy-packages)). **(Coming with GOV-003)** A Block finding stops promote or deploy until it's fixed or an exception is approved.

## Worked examples

The ids below are illustrative; use your own. **Checked by** names rules from the registry ([the rules](governance.md#the-rules)); a name no checker has is listed as not checked.

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
| Parameters | `max_length = 60` (stricter: lower), `pattern = "^[a-z][a-z0-9_]*$"` (a regular expression, fixed), `layers = ["silver", "gold"]` (stricter: more items) |
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

Check now checks the first half (every Gold table classified); the Restricted-in-Public-product half has no rule yet. Classification here is the catalog's own (Public, Internal, Confidential, Restricted). It is not a Microsoft Purview sensitivity label. Labels, and what Fabric does with them (DLP, downstream inheritance), stay in Purview.

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

The save gate already requires an owner for custom code (see the table below), so for that part the policy records the standard the app already holds. Check now checks both parts and warns; blocking comes with GOV-003.

### 4. From a template: a data contract

Your standard: every published data product in Sales loads within 6 hours, records its row count, and links its business terms in Purview. The built-in **Data contract** template already says most of it.

1. **Platform > Governance > Templates**, **Data contract** > **Use**. Give the policy the id `sales-contract` and the name `Sales data contract`. A draft is made from the template's definition:

   | Field | Value |
   | --- | --- |
   | Category | Data contract requirements |
   | Checked by | `contract.freshness, contract.row_count, contract.terms_linked` |
   | Parameters | `within_hours = 24` (stricter: lower), `min_rows = 1` (stricter: higher) |

2. **Edit draft**: set `within_hours` to `6`, then **Publish** and **Assign** it to the Sales domain.
3. Open a Sales project and choose **Check this project** > **Check now**. Each published product is checked: when it last loaded (from the run logs), the row count that load recorded, and its business terms (from the platform's catalog). A product that hasn't loaded yet shows **!** with why, never a pass.
4. The product's page (Data mesh > Products) now shows its **Checklist** from this policy: **Fresh within 6 h**, alongside owner and steward, description, business terms and endorsement.
5. Happy with it? On the policy's page, **Save as template** keeps your version (`sales-contract-template`, tagged **Yours**) so other domains can start from it.

Template changes are recorded in `catalog.policy_template` and **Platform > Audit**; **Use** writes only a policy draft.

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

When a standard genuinely doesn't fit, an exception records why, who approved it and until when.

- **Registry policies (Preview).** Ask on **Data mesh > Requests > New request > An exception to a policy**: the policy, the project, domain or workspace it's for, until when (a year at most) and why. A policy whose definition doesn't allow exceptions refuses the request. A platform admin approves it; the platform records it in `catalog.policy_exception`, the audit and the policy's history. It shows on the policy's **Where it applies** tab, the policy list counts it, and **Explain** for a project it covers says **Excepted until <date>: <why>**. It ends on its own on its date. **Check now** (Platform > Governance > Check this project) applies it: a finding the exception covers is marked excepted, showing **Excepted until <date>: <why>**, instead of counting. One that ends within 14 days warns whoever asked for it and the project it covers (a bell notice, a Data mesh banner and, with a platform Teams route set, one Teams notice). A platform admin can **End early**, with a reason: the exception stops applying now, its row stays with who ended it, when and why, and the audit and the policy's history record it. **Include ended** on the policy's exceptions lists the ended ones.
- **The save gate's rules** keep their exceptions in a project's `exceptions.toml` (see [the metadata guide](metadata-guide.md#6-quality-checks-quarantine-and-the-four-gates)).

## When the platform can't be reached

Checks that depend on the platform fail closed. When a rule can't be read, KratosForge stops the step and says why instead of acting as if there were no rule (see [Governance policies](governance.md)).

**Check now** is the exception that still works, because it carries the rules with it: the [policy package](governance.md#offline-policy-packages) it keeps on your PC has the project's policies, their published versions with their SHA-256 hashes, the assignments and the active exceptions, as of when it was taken. Offline it uses that package and says how old it is; it reads nothing from the platform, Fabric or OneLake, so rules that need the catalog or the run logs say unknown. A package whose hashes don't match is refused, never used. The hash check catches a changed or broken file; it isn't a signature, since signing would need a key the app doesn't have. The package is the platform's state when it was taken, the project's domain included: an exception ended early still applies offline until the package is taken again.

For CI, a policy owner takes a package where the platform can be reached and pins its SHA-256 where a pull request can't change it, in a protected CI variable; the check refuses any other package, so editing the file and recomputing its hashes doesn't get past it:

```
fabric-desktop governance package --out policies.json      # prints the SHA-256 to pin
# protected CI variable: KRATOSFORGE_POLICY_PACKAGE_SHA256=<that hash>
fabric-desktop governance check --project . --package policies.json --max-age 30
```

Without a protected variable, put `policies.json` under `CODEOWNERS` so a change to it needs a policy owner's review, and pass `--expect-sha256` from a pipeline file the same owners protect. The check exits 1 when an Error finding (from a high or critical policy) isn't covered by an exception, and 2 when it can't check, the package isn't the pinned one, or it's older than `--max-age` days. See [offline policy packages](governance.md#offline-policy-packages).

## Teams notices for requests and shortcuts

With a platform Teams route set (**Settings > Notifications**), KratosForge posts:

| When | Kind | For |
| --- | --- | --- |
| A request is made (a key table, a source, access to a data product, a shortcut, a workspace, access to a workspace, a domain, an exception to a policy) | `request.waiting` | Platform admins, and the data product's owners when it names one |
| It is approved or declined | `request.decided` | Whoever asked |
| It is withdrawn | `request.withdrawn` | Platform admins and the product's owners |
| A health check on **Shortcuts > Health** finds a shortcut broken, or fixed again | `shortcut.broken`, `shortcut.fixed` | Platform admins |
| A data product is published as deprecated, or with a new `deprecated_on` (see [Data mesh](data-mesh.md#data-products)) | `product.deprecated` | The teams of the projects that link to it, and whoever saved each link |

| Someone presses **Tell them** under **Who's affected** on an incident page ([user guide](user-guide.md#whos-affected-preview)) | `incident.impact` | The owners of the data products built on the incident's table, people who linked a project to them, and followers |
| A promote to Prod deprecates a data product, changes its version, or adds, removes or retypes a port's columns (Preview) | `product.changed` | The product's followers and whoever linked a project to it. The message says what changed; a deprecation already sent as `product.deprecated` isn't repeated |
| A policy exception ends within 14 days (sent when someone opens Data mesh that day) | `exception.ending` | Whoever asked for it, and the team of the project it covers or the owners of its domain. Posted once per exception and end date for the whole organization (the platform records it on the exception); its reason stays in the app |

- **Only what the channel may read.** A message names the request or shortcut and where to open it in the app. A request's reason and detail stay in the app. People are named by UPN. Anything that looks like a secret, a webhook address among them, is masked before it's stored or posted.
- **Once each.** A request waiting, or a shortcut that stays broken, is posted once. A fix is posted only after its break was. A deprecation is posted once per date.
- **Never in the way.** The request or check is made first. If the notice can't be sent, the change still stands and the app says the notice wasn't sent. Messages Teams doesn't take wait in the outbox and go out later.
