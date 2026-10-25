# Governance policies

**Preview.** KratosForge keeps your organization's governance standards in one place, the platform's catalog, so they are defined once and every project is checked against the same thing. This page covers the policy registry (GOV-001): writing, publishing and assigning policies, and how a project's policies are worked out. [Best practices: checks and templates](#best-practices-checks-and-templates-preview) adds reusable policy templates and **Check now**, which checks a project against its policies on your PC (warn only). Blocking promote or deployment and the later governance features build on it. For a walk-through with worked examples, and what can and can't be enforced today, see the [governance guide](governance-guide.md).

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
| **Enforcement** | Advisory (shown only), Warn (shown as a warning) or Block. Block is not enforced yet: the app shows it as **Block (not enforced yet)**. Check now reports every policy's findings the same way, as warnings, and stops nothing. |
| **Mandatory** | When on, nothing below where the policy is assigned can make it weaker. |
| **Can be assigned at** | Which of enterprise, domain, project and entity it may be assigned to. |
| **Applies to** | Optional: only some layers or environments. |
| **In force** | Optional dates it takes effect and expires. |
| **Exceptions** | Whether people may ask for an exception (with a reason, an approver and an expiry, as in `exceptions.toml`). |
| **Checked by** | The ids of the rules that check it, separated by commas, like `contract.freshness, contract.terms_linked`. Check now runs these rules ([the rules](#the-rules)). A policy with none is listed as not checked. |
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

## Best practices: checks and templates (Preview)

### Templates

**Platform > Governance > Templates** lists templates: reusable policy definitions, in exactly the same shape as a policy. Each is tagged **Built in** or **Yours** (your organization's).

| Built-in template | Category | What it says | Checked by | Parameters |
| --- | --- | --- | --- | --- |
| Table names | Naming | Gold tables are snake_case and start `dim_` or `fact_` | `naming.table_name` | `pattern` (a regular expression, fixed), `layers` (more is stricter) |
| Every product has an owner | Ownership | A published product names an owner and a steward | `ownership.product_owner` | `steward_required` |
| Sensitive columns are labelled | Classification | Columns marked PII or PHI carry a sensitivity label | `classification.sensitive_columns` | `markers` (the column tags that mark sensitive data), `labels` (classifications that count as a label) |
| Data contract | Data contract | Fresh within N hours, row count checked, business terms linked | `contract.freshness`, `contract.row_count`, `contract.terms_linked` | `within_hours` (lower is stricter), `min_rows` |

- **Use** makes a new policy draft from a template (a policy id and name, then the usual draft, publish and assign). It needs a platform admin, as every new policy does.
- **Save as template**, on a policy's page, copies the policy's current published definition (its draft if it was never published) into a new template.
- **New template**, **Edit** and **Delete** (which asks first) work on your organization's templates. Built-in templates ship with the app and can't change.
- **Who can add templates.** Platform admins, by default. A platform admin can let domain owners add templates too (**Who can add templates** on the Templates view); a domain owner then edits and deletes only the templates they added. The platform checks this on every call.

Your organization's templates are kept in `catalog.policy_template` in the platform's catalog. Each add, edit and delete is in **Platform > Audit** (`policy template created`, `edited`, `deleted`), and so is changing who can add them (kept in `config.setting`). The table is created by **Finish or repair** and on the first template call. With the platform in Fabric, `UDF_Platform` gains one function per template operation; on an older `UDF_Platform` the app says: "After an update, a platform admin runs Platform > Settings > Finish or repair once."

### Check a project against its policies

**Platform > Governance > Check this project** checks the open metadata project. **Check now**:

1. Works out the policies in force for the project, exactly as **Explain** does: the platform's assignments plus the project's `governance.toml`.
2. Runs each policy's **Checked by** rules on this PC, with the policy's parameters as the rules' thresholds. A policy's severity sets its findings': high or critical is **Error**, medium **Warning**, low **Info**. A `severity` parameter overrides it for the rules, and `enabled = false` switches them off.
3. Applies exceptions: a finding an active exception covers shows **Excepted until <date>: <why>** instead of counting. **Ask for an exception** sends the request ([Platform > Requests](platform.md#requests)), with the policy and project filled in.
4. Shows the result, like "Sales Forecasting: 2 to look at" and "Checked on this PC 5 min ago. Warn only: nothing is blocked."

Each finding names its policy and what's wrong. Where a safe fix exists in the app it's offered: **Label it** sets a column's classification in the platform's catalog, after asking and saying what changes. Otherwise the finding links to where to fix it (**Open the entity**, **Edit the product**, **Add a term**). Renaming an entity isn't built, so a naming finding links to the entity instead. A passing policy shows a ✓ and how many objects it checked.

**Never guessed.** When something a rule needs isn't recorded anywhere (a product that hasn't loaded yet, terms on a product not yet registered in the catalog, endorsement no tenant scan has read), the rule says **unknown** with the reason, never a pass. So does a rule whose settings it can't read (a `min_rows` of "ten", say): the other rules still run. Naming patterns are matched with RE2 (the engine DuckDB uses), whose time grows only with the name's length, so no pattern can stall Check now. RE2 has no lookarounds or backreferences: a pattern that uses them says **unknown** and why. Patterns over 300 characters and names over 256 characters aren't matched.

**What it reads and writes.** It reads the project's files on this PC, the platform's catalog (policies, exceptions, catalog text, product terms and endorsement) and the run logs Monitor already reads (Test and Prod only when you're signed in). It writes only its last result, to `governance_checks.json` in the app's data folder, so "Checked ... ago" works. It runs no Fabric job.

**Where the data comes from.**

| Rule | Reads |
| --- | --- |
| `naming.table_name` | The entity's name |
| `ownership.product_owner` | `owner` and `steward` in `metadata/products/<name>.toml` (the product form has a **Steward** field) |
| `ownership.entity_owner` | `owner` in the entity's file |
| `classification.sensitive_columns` | A column is marked by `tags` (like `tags = ["pii"]`) or `pii = true` / `phi = true` in `[catalog.columns.<name>]`, or by tags saved in the catalog. Its label is its classification, the catalog's own (Public, Internal, Confidential, Restricted), not a Purview sensitivity label |
| `classification.required` | The entity's classification |
| `contract.freshness`, `contract.row_count` | The run logs: the last successful run of each of the product's port entities, in the highest environment where all of them ran. Row count is the rows that run recorded writing |
| `contract.terms_linked`, `contract.endorsed` | `catalog.product_term` and `catalog.endorsement` ([endorsement and business terms](data-mesh.md#endorsement-and-business-terms)) |
| `contract.description` | The product's description |

`contract.endorsed` passes only when every one of the product's items is endorsed with an accepted value; a finding names the ones that aren't.

**Checkers that run in Fabric** are never started by Check now. Their last stored findings show with their date, or **not run**.

### A data product's checklist

Open a product on **OneLake > Data products**: its **Checklist** card comes from the contract it follows, the first policy in force for its project whose **Checked by** has a `contract.` rule, or else the built-in Data contract template (the card says which). Each line is ✓, ✗ or **!** (unknown, with why): **Owner and steward**, **Description**, **Business terms linked**, **Fresh within N h** (the last load time) and **Endorsement** (unknown means it needs a tenant scan). The steward and load times are kept in the product's project, so for another team's product they show as unknown until that project is open on this PC. The card only reads.

## When the platform can't be reached

Checks that depend on the platform fail closed: when a rule can't be read, KratosForge stops the action and says why, rather than acting as if there were no rule. Today that covers the [shortcut rules](shortcuts.md#shortcut-rules), and who-can-read lists, which say **Not complete** when a lakehouse's data access roles can't be read. Policy checks (GOV-002 onward) will follow the same rule.

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

The desktop calls these engine methods (JSON-RPC, see [architecture](architecture.md)): `governance_overview`, `governance_policy`, `governance_version`, `governance_create`, `governance_save_draft`, `governance_discard_draft`, `governance_publish`, `governance_set_status`, `governance_assign`, `governance_unassign`, `governance_applicability`, `governance_resolve`; for templates and checks `governance_templates`, `governance_template_save`, `governance_template_delete`, `governance_template_use`, `governance_template_from_policy`, `governance_template_authors`, `governance_check_now`, `governance_last_check` and `governance_product_checklist` (`central/checks_service.py`). The template operations are in `policy.py` (`list_policy_templates`, `save_policy_template`, `delete_policy_template`, `use_policy_template`, `save_policy_as_template`, `set_template_authors`), each with a `UDF_Platform` function.

### The rules

Checks come from one registry, `sidecar/fabric_desktop/governance_checks.py`. The built-in checkers (naming, ownership, classification, contract) are registered there; other checkers (the Best Practice Analyzer's semantic model, notebook and SQL checks) plug in the same way. A policy points at rules by id in **Checked by**.

```python
register(kind, rules, check, runs_in="local")   # runs_in: "local" (this PC) or "fabric"
```

- `rules()` returns dicts: `id`, `kind` (the item kind it checks: entity, product, notebook, ...), `severity` (`Error`, `Warning` or `Info`), `category`, `name`, `description`, and optionally `fix`, `url`, `default_thresholds` (`{name: value}`) and `scheduled_only` (only a scheduled run checks it, never Check now).
- `check(item, settings)` gets one item (a dict whose `kind` says what it is) and `{rule_id: {"enabled": bool, "severity": "Error"|"Warning"|"Info", "thresholds": {name: value}}}` for the rules a policy turned on. It returns a list of findings, or `None` when none of those rules applies to the item (so it isn't counted as a pass).
- A finding: `rule_id`, `severity`, `rule` (the rule's name), `category`, `object_type`, `object`, `description`, `url`; optionally `cell` and `line` (carried through as they are), `fix` (`{"action", "label", ...}`, an action the app offers) and `outcome: "unknown"` when what the rule needs isn't recorded (the description says why). `finding(rule, settings, object_type, object, description, **extra)` builds one.
- A policy's parameters become the settings: `enabled` and `severity` are read as such, every other parameter is a threshold over the rule's `default_thresholds`. Severity maps both ways: Error is high (or critical), Warning medium, Info low.
- A checker with `runs_in="fabric"`, or a `scheduled_only` rule, is never run by Check now; `store_fabric_findings(state, project, rule_id, findings)` keeps what a Fabric run found, and Check now shows it with its date.

A minimal checker:

```python
from fabric_desktop import governance_checks as gc

RULES = [{"id": "notebook.no_print", "kind": "notebook", "severity": "Info", "category": "quality",
          "name": "No print()", "description": "Notebooks log instead of printing."}]

def check(item, settings):
    s = settings.get("notebook.no_print")
    if not s or not s["enabled"]:
        return None
    return [gc.finding(RULES[0], s, "notebook", item["id"], "print() in a cell", cell=n, line=1)
            for n, cell in enumerate(item["cells"]) if "print(" in cell]

gc.register("notebook-example", lambda: RULES, check)
```

A policy whose **Checked by** is `notebook.no_print` then runs it on every item of that kind Check now is given. Today Check now builds entity and product items; an adapter that builds notebook items is the notebook checker's own.

## What isn't built yet

- Blocking promote or deployment on a **Block** policy (later GOV items). Check now is warn only: it reports and stops nothing.
- Check now checks the open project at project scope: a policy assigned to one entity only isn't checked yet, nor are items made outside the app.
- Renaming an entity from a finding (a naming finding links to the entity instead), and fixes beyond **Label it**.
- Checkers that run in Fabric, and storing their findings from a Fabric run: the registry accepts them and Check now shows what's stored, but nothing stores any yet.
- A product's steward isn't in the platform's catalog, so another team's product shows it as unknown.
- Signed policy packages for offline use (GOV-002).
- Project exceptions in `exceptions.toml` (the save gate's) are unchanged and separate from policy exceptions, which Check now applies.
- A separate approver for publishing (the same admin can write and publish).
- Not yet tried against `SQL_Platform` in a live Fabric tenant (hence Preview).
