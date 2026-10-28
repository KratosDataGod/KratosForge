# Governance policies

**Preview.** KratosForge keeps your organization's governance standards in one place, the platform's catalog, so they are defined once and every project is checked against the same thing. This page covers the policy registry (GOV-001): writing, publishing and assigning policies, and how a project's policies are worked out. [Best practices: checks and templates](#best-practices-checks-and-templates-preview) adds reusable policy templates and **Check now**, which checks a project against its policies on your PC. A policy set to **Block** stops a promote ([Block stops promote](#block-stops-promote)). Blocking deployment and the later governance features build on it. For a walk-through with worked examples, and what can and can't be enforced today, see the [governance guide](governance-guide.md).

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
| **Enforcement** | Advisory (shown only), Warn (shown as a warning) or Block. Check now lists every policy's findings the same way and stops nothing. A finding of a Block policy that no exception covers stops a promote ([Block stops promote](#block-stops-promote)); Advisory and Warn never do. |
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
| **Platform admins** | Create, edit, publish, deprecate, retire and discard policies; assign them at any scope; grant exceptions (by approving a request) and end them early, with a reason |
| **A domain's owners** | Assign published policies to their domain and its subdomains, and change or remove those assignments (tightening only under a mandatory policy) |
| **Everyone** | Read every policy, version, assignment and history, and see how policies resolve for any project |

No new Fabric permission or sign-in scope is needed: policies use the same `UDF_Platform` calls as the rest of the platform ([permissions](permissions.md)).

## In the app

**Platform > Governance** lists policies with search, category and status filters and counts by status. Open a policy for:

- **Definition:** the selected version, with every version listed beside it (current, older, draft) and whether each is intact.
- **Where it applies:** its assignments (**Assign**, **Change**, **Remove**) and every registered project it reaches; **Explain** shows how it was worked out for that project, including the open project's `governance.toml`.
- **History:** every change, who made it and when.

Admins see **New policy**, **Edit draft**, **Publish**, **Deprecate** and **Retire**. **Save and publish** saves the draft and publishes it in one step.

### Exceptions to a policy

Anyone asks for an exception on **Data mesh > Requests** (or **Ask for one** on a policy's **Where it applies** tab); a platform admin grants it by approving the request. The **Exceptions in force** card on **Where it applies** lists each one: who and what it's for, until when, its status, why, and who granted it and who asked.

- **Status.** **In force**, **Ends in N days** when it ends within 14 days (the same window as a project's `exceptions.toml`), **Ended** after its date, or **Ended early**, with when, by whom and why.
- **A warning before it ends.** An exception that ends within 14 days shows a banner on Data mesh and a notice on the bell, once a day, for the person who asked for it and for anyone with the project (or a project in the domain) it covers open. It's also posted once to Teams as **Policy exception ending** (kind `exception.ending`, to the requester by UPN and the project's team or the domain's owners by role) when a platform Teams route is set; like a request, its reason stays in the app. Before posting, a PC claims the notice in the platform (`claim_exception_warning`: one conditional UPDATE that sets the exception's `warned_until` to its end date), so one PC in the organization posts it, not one per PC. A PC claims only when one of its own Teams routes would take the notice (its kind, and the exception's domain where a route is limited to domains), so a PC with no such route never takes it from one that has one; an exception already marked as warned for its end date isn't asked about again. If the claiming PC's post then fails (Teams refuses it, or it waits in that PC's outbox), no other PC posts that notice. Anyone who can call the platform can claim, so a user could stop the Teams post; the bell and banner still show the exception to everyone it concerns. With an older `UDF_Platform` that can't claim, no Teams notice is posted; the bell and banner still show. The policy list's count says how many are ending soon.
- **End early.** A platform admin chooses **End early** on an exception in force and gives a reason (required, up to 500 characters). The platform checks that the caller is a platform admin, refuses an exception that doesn't exist or has already ended, and then updates the exception's row: its end moves to yesterday, so it stops applying today wherever it's read, and `ended_at`, `ended_by`, `end_reason` and `granted_until` record what happened. The row is never deleted. It writes one entry to the audit (`ended exception`) and one to the policy's history (`exception ended early`, which the audit records too). The reason is visible to anyone who can read the policy's history, which is everyone. Check now and **Explain** stop treating the place as excepted at once.
- **Include ended** on the card also lists exceptions that ended on their date or early, so the record of each grant stays readable in the app.

An older `UDF_Platform` without the end operation says so: a platform admin runs **Platform > Settings > Finish or repair** once.

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

Your organization's templates are kept in `catalog.policy_template` in the platform's catalog. Each add, edit and delete is in **Platform > Audit** (`policy template created`, `edited`, `deleted`), and so is changing who can add them (kept in `config.setting`). The table is created by **Finish or repair** and on the first template call. A [platform repository](source-control.md#platform-repository) keeps them in Git as `policy_templates/<id>.toml` (yours only; built-in ones aren't exported); its deploy adds or changes them and never deletes one. Who can add templates isn't in Git yet. With the platform in Fabric, `UDF_Platform` gains one function per template operation; on an older `UDF_Platform` the app says: "After an update, a platform admin runs Platform > Settings > Finish or repair once."

### Block stops promote

A promote (Environments > **Promote Dev to Test**, **Promote Test to Prod**) runs the same checks as Check now for the project's **Block** policies, on this PC, before anything moves. Its existing list of things to do first gains one line per finding, naming the policy and the finding, like `Block policy "Sensitive columns are labelled": silver.customers#email: ...`. Up to 10 findings per policy are listed; Check now lists them all. No new screen.

- **Warn and Advisory never stop a promote.** Check now still lists their findings.
- **An exception lets it through.** A finding an active exception covers (as in Check now) doesn't stop the promote. The promote's notes name the policy, how many findings the exception lets through and the exception (**Excepted until <date>: <why>**, and who approved it), and the promote's result keeps the same under `policies`.
- **Per environment.** The policies are worked out for the target environment, so a policy whose **Applies to** lists only Prod stops a promote to Prod and not to Test.
- **Without the platform.** When the platform can't be reached (no connection), the latest [policy package](#offline-policy-packages) on this PC stands in, and the notes say which and how old. For a promote to Prod it must have been taken within the last day: an older one may miss a Block policy assigned since, so Prod stops instead. Test and Dev take any package whose hashes match. A platform that answers with an error is never stood in for by an older package.
- **Fails closed for Prod.** When the policies can't be read at all (the platform can't be reached and there's no package on this PC, or, for Prod, none taken within the last day, or it was changed after it was taken, or the platform answers with an error), a promote to Prod stops and says why. A promote to Test or Dev only warns. KratosForge can't know which policies are assigned without reading them, so a Prod promote stops whenever they can't be read, not only when a Block policy is known to be assigned.
- **What doesn't stop it.** A rule that says **unknown** (what it needs isn't recorded, or isn't read offline) and a checker that runs in Fabric are notes, not reasons: neither stops the promote.

It reads what Check now reads, and writes nothing: Check now's last result and the policy package on this PC stay as they are. Like the rest of the promote gate, it holds for promotes through KratosForge; it can't stop someone writing to a workspace in the Fabric portal.

### Check a project against its policies

**Platform > Governance > Check this project** checks the open metadata project. **Check now**:

1. Works out the policies in force for the project, exactly as **Explain** does: the platform's assignments plus the project's `governance.toml`.
2. Runs each policy's **Checked by** rules on this PC, on the project's entities and products and, when a policy names their rules, this PC's notebooks and SQL scripts, with the policy's parameters as the rules' thresholds. A policy's severity sets its findings': high or critical is **Error**, medium **Warning**, low **Info**. A `severity` parameter overrides it for the rules, and `enabled = false` switches them off.
3. Applies exceptions: a finding an active exception covers shows **Excepted until <date>: <why>** instead of counting. **Ask for an exception** sends the request ([Data mesh > Requests](platform.md#requests)), with the policy and project filled in.
4. Shows the result, like "Sales Forecasting: 2 to look at" and "Checked on this PC 5 min ago. Check now stops nothing. A promote stops on a finding of a Block policy that no exception covers.", and where the policies came from: the platform, or (offline) which policy package and how old it is.

Each finding names its policy and what's wrong. Where a safe fix exists in the app it's offered: **Label it** sets a column's classification in the platform's catalog, after asking and saying what changes. Otherwise the finding links to where to fix it (**Open the entity**, **Edit the product**, **Add a term**). Renaming an entity isn't built, so a naming finding links to the entity instead. A passing policy shows a ✓ and how many objects it checked.

**Never guessed.** When something a rule needs isn't recorded anywhere (a product that hasn't loaded yet, terms on a product not yet registered in the catalog, endorsement no tenant scan has read), the rule says **unknown** with the reason, never a pass. So does a rule whose settings it can't read (a `min_rows` of "ten", say): the other rules still run. Naming patterns are matched with RE2 (the engine DuckDB uses), whose time grows only with the name's length, so no pattern can stall Check now. RE2 has no lookarounds or backreferences: a pattern that uses them says **unknown** and why. Patterns over 300 characters and names over 256 characters aren't matched.

**What it reads and writes.** It reads the project's files on this PC, the platform's catalog (policies, exceptions, catalog text, product terms and endorsement) and the run logs Monitor already reads (Test and Prod only when you're signed in). When the platform can't be reached it reads the latest [policy package](#offline-policy-packages) on this PC instead. It writes its last result, to `governance_checks.json` in the app's data folder, so "Checked ... ago" works, and, when the platform answers, the project's policy package, to `governance_packages/<project>.json` there. It runs no Fabric job.

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

`contract.endorsed` passes only when every one of the product's items is endorsed with an accepted value; a finding names the ones that aren't. Its items are the lakehouses the product's ports live in. Semantic models and reports grouped under a product (`catalog.product_item`, [Tables, semantic models and reports](data-mesh.md#tables-semantic-models-and-reports-preview)) show their own badges on its page and don't change this check.

**Checkers that run in Fabric** are never started by Check now. Their last stored findings show with their date, or **not run**.

### Offline: policy packages

A **policy package** is one JSON file with what checking a project needs from the platform, so the check can run without it (GOV-002):

- the project, when the package was taken and from which platform (the SQLite platform on this PC by its file name, or `UDF_Platform` by its workspace and item ids);
- the scopes the project inherits from (enterprise, its domain and subdomain, the project);
- the policies in force when it was taken, each with its published version and that version's stored SHA-256 (the hash `catalog.policy_version` keeps, see [Storage](#storage));
- those policies' published versions, each definition with its stored SHA-256, and the platform's assignments on those scopes;
- the exceptions active when it was taken;
- the package's own SHA-256, over everything above.

**Taken and refreshed.** Each **Check now** that reaches the platform takes the package again and keeps it as `governance_packages/<project>.json` in the app's data folder, replacing the last one. A version whose definition doesn't match its stored SHA-256 in the platform is never packaged: the package isn't refreshed and the result says why. `fabric-desktop governance package` takes it on request; `--out <file>` also writes it to a file, such as one a CI job reads.

**Offline Check now.** When the platform can't be reached, **Check now** uses the latest package on this PC. The result says so, with which package, from which platform and how old it is: "Checked offline with the policy package taken 2 days ago (2026-10-26 09:00 UTC) from ...". The policies are resolved from the package exactly as the platform resolves them, with the project's current `governance.toml` and today's date, so an exception or policy that has passed its date since is no longer applied. Everything else is as it was when the package was taken: the project's domain and subdomain (the scopes it inherits from) are fixed then, and so are the platform's assignments and exceptions. An exception a platform admin **ends early**, or a policy changed or assigned since, keeps the package's state until the package is taken again; the result's age says how old that state is. Offline, nothing calls the platform, Fabric or OneLake: catalog text isn't read (only the text in the project's files is checked), and the rules that need product terms, endorsement or the run logs say **unknown**, with why. With no package on this PC and no platform, Check now says so and checks nothing.

**Tamper check.** A package is checked before it's used: its own SHA-256, each version's stored SHA-256 against its definition, and each policy in force against the version it names. A package that doesn't match (edited, cut short, not JSON, or for another project) is refused with what didn't match, and is never used as if valid. This is **hash integrity, not a signature**: it catches an accidental or partial change, but someone who edits a definition and recomputes the hashes isn't caught. Signing needs a key, and KratosForge has no app registration to hold one, so packages aren't signed. Someone who can edit the package can also lower a policy's severity, or remove a policy, and recompute every hash. So a check that matters (a CI gate) **pins** the package: `--expect-sha256 <hash>` (or the `KRATOSFORGE_POLICY_PACKAGE_SHA256` environment variable) refuses any package whose SHA-256 isn't that one, before anything else is read from it. Keep the pinned hash where the people whose work is checked can't change it (see [the CI recipe](#offline-policy-packages)).

**From the command line.** `fabric-desktop governance check` runs the same checks with no platform, Fabric or OneLake call, prints each policy and finding, and sets the exit code for CI:

```
fabric-desktop governance check [--package <file>] [--project <folder>] [--json]
```

- `--package`: the package to use (default: the latest one on this PC for the project).
- `--project`: the metadata project's folder (default: the project open in the app).
- `--expect-sha256 <hash>`: refuse the package unless its SHA-256 is this one (default: the `KRATOSFORGE_POLICY_PACKAGE_SHA256` environment variable, when it's set).
- `--max-age <days>`: refuse a package taken more than this many days ago.
- `--require-pin`: refuse to check (exit 2) when no SHA-256 is given, so a missing or misspelt CI variable can't run the check unpinned. Without it, an unpinned check says **Not pinned** in its output and `"pinned": false` in `--json`.
- `--json`: the whole result as JSON, with `unexcepted_errors`.
- Exit code: 0 when no finding fails, 1 when an **Error** finding (a high or critical policy) isn't covered by an exception, 2 when it can't check (no project, no package, a package that doesn't match, isn't the pinned one or is too old).

The command keeps no result: it isn't saved as the app's last check.

**In CI.** A pull request's author can edit any file in the pull request, a package included, so the package's hash is what's trusted, not the file:

1. A policy owner takes the package on a machine that reaches the platform: `fabric-desktop governance package --out policies.json`. It prints the package's SHA-256.
2. They commit `policies.json` and store the SHA-256 in a protected CI variable (`KRATOSFORGE_POLICY_PACKAGE_SHA256`), one only they can change and that a pull request's pipeline reads but can't set.
3. The pipeline runs `fabric-desktop governance check --project . --package policies.json --max-age 30 --require-pin`. A changed package, even with every hash recomputed, isn't the pinned one, so the check refuses it (exit 2).

When the policies change, the owner takes a new package and updates the variable together. Where a protected variable isn't available, protect the file instead: a `CODEOWNERS` entry for `policies.json` so a change to it needs a policy owner's review, with branch protection requiring that review, and pass the hash with `--expect-sha256` from the pipeline definition the same owners protect.

### A data product's checklist

Open a product on **Data mesh > Products**: its **Checklist** card comes from the contract it follows, the first policy in force for its project whose **Checked by** has a `contract.` rule, or else the built-in Data contract template (the card says which). Each line is ✓, ✗ or **!** (unknown, with why): **Owner and steward**, **Description**, **Business terms linked**, **Fresh within N h** (the last load time) and **Endorsement** (unknown means it needs a tenant scan). The steward and load times are kept in the product's project, so for another team's product they show as unknown until that project is open on this PC. The card only reads.

## When the platform can't be reached

Checks that depend on the platform fail closed: when a rule can't be read, KratosForge stops the action and says why, rather than acting as if there were no rule. Today that covers the [shortcut rules](shortcuts.md#shortcut-rules), and who-can-read lists, which say **Not complete** when a lakehouse's data access roles can't be read. **Check now** uses the latest [policy package](#offline-policy-packages) on this PC when the platform can't be reached, says which and how old, and refuses one whose hashes don't match; with no package it checks nothing and says why.

## Storage

Policies are four tables in the platform's catalog, next to domains and the glossary (no second database):

| Table | Holds |
| --- | --- |
| `catalog.policy` | One row per policy: its latest name, category, owner, status, current and draft version |
| `catalog.policy_version` | Every version: draft or published, its definition (JSON), SHA-256 hash, note, who and when |
| `catalog.policy_assignment` | Where each policy is assigned, and the settings it has there |
| `catalog.policy_event` | Each policy's history |
| `catalog.policy_exception` | Granted exceptions: the policy, scope and target, until when, the reason, who asked and who approved; for one ended early, `ended_at`, `ended_by`, `end_reason` and `granted_until` (the date it was granted until); `warned_until`, the end date whose Teams notice was claimed. Rows are never deleted |

The platform's schema version goes from 3 to 4. The tables are created by **Finish setup** (as every platform table is), and also on the first policy call to a platform that doesn't have them yet, so existing platforms and projects keep working without a migration step. A policy's definition is stored as JSON, so new fields can be added later without changing the tables.

For Fabric, `UDF_Platform` gains one function per policy operation. Re-run **Platform > Settings > Finish or repair** to update the function app.

## For developers

The rules live in `sidecar/fabric_desktop/central/policy.py`, standard library only, next to `core.py`; `udf.py` builds the function app from both. `resolve(snapshot, chain, local, context, today)` is a pure function over plain data (`policy_snapshot` returns that data), so offline validation runs it from a policy package: `central/policy_package.py` takes (`take`), checks (`verify`, `read`) and resolves (`resolve_offline`) packages, standard library only and with no platform call. `ChecksMixin.governance_check_from_package(package)` is Check now from a package (the CLI's `governance check`), and `governance_package_export(out)` takes one (`governance package`); neither is an RPC method.

The desktop calls these engine methods (JSON-RPC, see [architecture](architecture.md)): `governance_overview`, `governance_policy`, `governance_version`, `governance_create`, `governance_save_draft`, `governance_discard_draft`, `governance_publish`, `governance_set_status`, `governance_assign`, `governance_unassign`, `governance_applicability`, `governance_resolve`, `governance_end_exception` (end an exception early; `policy.end_policy_exception`, with its own `UDF_Platform` function), `governance_exceptions_ending` (exceptions that end within 14 days; it claims each Teams notice with `policy.claim_exception_warning` and sends the ones it claimed); for templates and checks `governance_templates`, `governance_template_save`, `governance_template_delete`, `governance_template_use`, `governance_template_from_policy`, `governance_template_authors`, `governance_check_now`, `governance_last_check` and `governance_product_checklist` (`central/checks_service.py`). The template operations are in `policy.py` (`list_policy_templates`, `save_policy_template`, `delete_policy_template`, `use_policy_template`, `save_policy_as_template`, `set_template_authors`), each with a `UDF_Platform` function.

### The rules

Checks come from one registry, `sidecar/fabric_desktop/governance_checks.py`. The built-in checkers (naming, ownership, classification, contract) are registered there, and the notebook, SQL and semantic model best-practice checkers are registered the same way (below). The Best Practice Analyzer rules that need a model's VertiPaq statistics run only in Fabric and aren't registered, so a policy can't name them. A policy points at rules by id in **Checked by**.

```python
register(kind, rules, check, runs_in="local")   # runs_in: "local" (this PC) or "fabric"
```

- `rules()` returns dicts: `id`, `kind` (the item kind it checks: entity, product, notebook, ...), `severity` (`Error`, `Warning` or `Info`), `category`, `name`, `description`, and optionally `fix`, `url`, `default_thresholds` (`{name: value}`) and `scheduled_only` (it checks only items marked `"scheduled": true`, like a notebook a schedule on this PC runs).
- `check(item, settings)` gets one item (a dict whose `kind` says what it is) and `{rule_id: {"enabled": bool, "severity": "Error"|"Warning"|"Info", "thresholds": {name: value}}}` for the rules a policy turned on. It returns a list of findings, or `None` when none of those rules applies to the item (so it isn't counted as a pass).
- A finding: `rule_id`, `severity`, `rule` (the rule's name), `category`, `object_type`, `object`, `description`, `url`; optionally `cell` and `line` (carried through as they are), `fix` (`{"action", "label", ...}`, an action the app offers) and `outcome: "unknown"` when what the rule needs isn't recorded (the description says why). `finding(rule, settings, object_type, object, description, **extra)` builds one.
- A policy's parameters become the settings: `enabled` and `severity` are read as such, every other parameter is a threshold over the rule's `default_thresholds`. Severity maps both ways: Error is high (or critical), Warning medium, Info low.
- A checker with `runs_in="fabric"` is never run by Check now; `store_fabric_findings(state, project, rule_id, findings)` keeps what a Fabric run found, and Check now shows it with its date.

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

A policy whose **Checked by** is `notebook.no_print` then runs it on every item of that kind Check now is given. Check now builds entity and product items, notebook items (this PC's notebooks, their code cells, and `scheduled` when an enabled schedule on this PC runs the notebook, either a notebook schedule or a Notebook step of a pipeline schedule) when a policy names a notebook rule, SQL items (this PC's SQL scripts: your saved queries, those in the repositories opened in Source control, and the `.sql` files of the warehouse projects in those repositories) when a policy names a SQL rule, and the model Update model writes for the last layer when a policy names a semantic model rule.

The notebook rules `NB001` to `NB012` ([notebook best practices](notebook-best-practices.md)) the SQL rules `SQ001` to `SQ010` ([SQL best practices](sql-best-practices.md)) and the semantic model rules `SM001` to `SM007` ([semantic model rules](semantic-model-rules.md)) are registered this way (`best_practice_checkers.py`), so a policy can name them in **Checked by**, like `NB003, NB006`. Each runs only the rules its policy names, with the policy's severity, or `severity` and `enabled` parameters. `NB004` and `NB008` are for scheduled runs, so Check now runs them only on the notebooks a schedule on this PC runs: a notebook schedule's notebook, or a saved notebook a pipeline schedule's Notebook step runs (Fabric schedules aren't read, and a paused schedule doesn't count). SQL rules check each script as warehouse T-SQL; a script that can't be read is listed under the result's problems, not skipped silently. A policy whose rules found nothing of their kind (no notebooks, no scheduled notebooks or no SQL scripts on this PC) shows as not checked, never as passing, and so does one whose parameters turn its rules off (`enabled = false`).

## What isn't built yet

- Blocking promote or deployment on a **Block** policy (later GOV items). Check now is warn only: it reports and stops nothing.
- Check now checks the open project at project scope: a policy assigned to one entity only isn't checked yet, nor are items made outside the app.
- Renaming an entity from a finding (a naming finding links to the entity instead), and fixes beyond **Label it**.
- Checkers that run in Fabric, and storing their findings from a Fabric run: the registry accepts them and Check now shows what's stored, but nothing stores any yet.
- A product's steward isn't in the platform's catalog, so another team's product shows it as unknown.
- Signed policy packages. A package is hash-checked, not signed: signing needs a key, and there's no app registration to hold one. See [the tamper check](#offline-policy-packages).
- An offline check doesn't read the platform's catalog text, product terms and endorsement, or the run logs, so the rules that need them say unknown. It uses the assignments, exceptions and domain chain as they were when the package was taken: an exception ended early keeps applying until the package is taken again. There's no screen to take, pick or inspect a package: Check now refreshes it, and the CLI takes and uses it.
- Project exceptions in `exceptions.toml` (the save gate's) are unchanged and separate from policy exceptions, which Check now applies. Adding one records no request, approval or audit entry (PB-059 review finding 1).
- Changing an exception's date or scope: end it early and ask for a new one.
- A separate approver for publishing (the same admin can write and publish).
- Not yet tried against `SQL_Platform` in a live Fabric tenant (hence Preview).
