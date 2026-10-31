# The platform

The platform is what a central data team runs once so every other team can build on it: **one business key system** for every team, **one landing zone** where raw data from every source arrives, one catalog of domains and data products, and one record of runs. Each team then builds its own Bronze from the landing zone, and everything after it. KratosForge creates it for you, in Fabric, from **Platform > Settings**, and the **Platform** page is where the central team runs it and every other team asks for what it needs.

!!! note "Preview"
    The platform is built to Fabric's documented APIs but hasn't been tried on a live tenant yet. Create it in a test tenant or a non-production capacity first. See [What isn't proven yet](#what-isnt-proven-yet).

## What gets created

One **Platform workspace per environment** (Dev, Test, Prod), named `Fabric Desktop Platform - <Environment>` unless you choose another name. Each holds:

| Item | Type | What it's for |
| --- | --- | --- |
| `SQL_Platform` | SQL database | Surrogate keys (`keys.<name>`, until they move into [one keys database](#one-keys-database-for-dev-test-and-prod)) and the catalog (`catalog.*`: platform admins, key tables, landing zone sources and their readers, domains, projects, data products and their ports, links, access grants, requests, descriptions, glossary, and an audit trail of every change: each change an admin, owner or member makes, keys copied in by a move, requests sent, decided or withdrawn, projects recorded or changed, links, and shortcut scans and changes. Left off by design: keys issued by loads, which keys tables record, and following a product) |
| `UDF_Platform` | User data functions | The platform's API. KratosForge, notebooks, the headless agent and Power BI buttons all call these functions; nobody connects to the database directly |
| `LH_Landing` | Lakehouse | The one landing zone: raw files from every source system, in `Files/<source>/<entity>/`. Teams read it; they don't copy it |
| `LH_Meta` | Lakehouse | Run history from every project |
| `VL_Platform` | Variable library | The ids of these items, so a notebook can find them |

With [one landing workspace](#one-landing-workspace-for-dev-test-and-prod-preview) (Preview), the landing zone moves out of these workspaces into one workspace that every environment reads.

Domains, the glossary, data products and descriptions don't depend on the environment, so they live in the **Prod** platform. Keys are per environment until you move them into [one keys database](#one-keys-database-for-dev-test-and-prod).

Without a platform in Fabric, the same tables live in a SQLite file on your PC (`%LOCALAPPDATA%\FabricDesktop\platform\`), so one engineer can try domains, products and the catalog before the central team has set anything up. Moving to Fabric later doesn't change how anything works.

## Create it

1. Sign in with an account that can create workspaces on the capacity you'll use.
2. Open **Platform > Settings** and choose **Create** next to Prod (then Test, then Dev, if you want all three).
3. Pick a capacity. A SQL database and user data functions need a Fabric capacity (F2 or larger, or a trial).
4. Each step reports **done** or **failed**. A failed step says what to do; fix it and choose **Finish or repair**. Every step is safe to run again: what exists is kept.
5. You become the platform's first **platform admin**. Add the rest of the central team on **Platform > Settings**.

If the step **Platform tables** fails because `UDF_Platform` has no connection to the database, open `UDF_Platform` in Fabric, choose **Manage connections**, add `SQL_Platform` with the alias `platform`, publish, and choose **Finish or repair**.

## Join it from other PCs

Everyone else chooses **Join existing** next to each environment and picks the Platform workspace. KratosForge finds the items by name.

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
| **Platform admins** | Domains, business key tables, landing zone sources, shared glossary terms, governance policies and their exceptions, shortcut rules and accepted shortcut risks ([shortcuts.md](shortcuts.md#accept-a-crossed-environment)), the list of admins, and every request |
| **A domain's owners** (set on the domain) | Requests for a new data product from their domain, which projects join their domain, what's certified in their domain's projects, their domain's glossary terms, and which governance policies apply to their domain (tightening only) |
| **A project's owners** (Preview, [data-mesh.md](data-mesh.md#members-and-joining-a-domain-preview)) | The project's members. Its members publish its products, make its links and change its domain |
| **A data product's owner** | Who else may read their product (access requests), and requests for a change to it |
| **A project's members** | Its catalog text (descriptions, tags, classification, glossary terms, steward), its products and its links |
| **Everyone** | Requests |

The platform checks these itself on every call, as the person Fabric says is calling (a user data function reads the caller from Fabric, never from what the app sends). The app hides buttons you can't use, but that's only a convenience. Whoever sets the platform up is its first admin; a platform with no admin lets the first person who signs in claim it on **Platform > Settings**, and always keeps at least one. Admins are kept in each environment's `SQL_Platform`; adding or removing one on the Platform page changes all of them.

Every change is recorded with who made it and when (**Platform > Audit**). Actions and changes made from a workload page in Monitor (runs, cancels, owners, criticality, tags and links) are recorded there too, with before, after and how they ended ([Workloads](user-guide.md)). In Fabric, a platform admin runs **Platform > Settings > Finish or repair** once after this update, so `UDF_Platform` has the `log_workload_action` function; until then each PC keeps its lines and sends them afterwards.

## The Platform page

The rail's **Platform** page is the central team's home, and the same screens show every other team what the platform offers.

Its tabs are **Overview**, **Set up**, **Govern** and **Settings**, the way Data mesh lays out its tabs. **Set up** holds **Landing zone**, **Business keys** and **Glossary**, and **Govern** holds **Governance** and **Audit**: pick one on the switcher under the tabs. Requests and domains are on Data mesh, where every team works: **Data mesh > Requests** and **Data mesh > Domains**; older links to Platform's Requests and Domains open them there. In these docs, **Platform > Business keys** means Set up > Business keys.

- **Overview:** counts, requests that need you, each environment's platform, landing zone sources (late ones first in red) and recent changes.
- **Landing zone:** each source in `LH_Landing` for the environment picked at the top: its entities, newest files, last arrival, how often it's expected and which projects read it. A source that hasn't arrived within its expected hours (plus a quarter) shows **late**. Folders in the landing zone that no admin registered show **not registered**.
- **Business keys:** the platform's key tables. **New key table** (admins) makes it in Dev, Test and Prod at once, so every environment has the same tables.
- **Glossary:** your organization's shared terms. This is the only place they're created and edited.
- **Domains** moved to **Data mesh > Domains**, where everyone sees them with their owners, projects and products, and only platform admins change them; older links to Platform's Domains open it there. **Import from Fabric** brings in the domains and subdomains defined in Fabric; it needs a Fabric administrator. **New domain** creates the domain in Fabric too (Preview), unless you untick **Also create it in Fabric**: that also needs a Fabric administrator, and someone who isn't one is told so and can keep the domain in the platform only. A subdomain is created in Fabric only when its parent is in Fabric. A domain that follows Fabric offers **Edit owners** (its name and parent follow Fabric); delete it in Fabric first, or the next import brings it back. See [Domains come from Fabric](data-mesh.md#domains-come-from-fabric).

### Entra ID groups per domain (Preview)

Each domain row on **Data mesh > Domains** has **Access groups**: one Entra ID security group per role, so access goes to groups, never to people, and a joiner or leaver is one membership change.

| Role | Who goes in | What it gets in Fabric | Environments |
| --- | --- | --- | --- |
| Domain owners | The people accountable for the domain's data | Domain admin on the domain | All |
| Domain contributors | Workspace admins allowed to put workspaces in the domain | Domain contributor on the domain | All |
| Workspace admins | The domain's platform leads (keep Prod to a few named people) | Admin on the domain's workspaces | Dev, Test, Prod |
| Creators | Data engineers and analysts who build | Contributor on the domain's workspaces | Dev, Test |
| Data readers | Other teams approved to read the domain's data products | Read through a OneLake data access role; no workspace role | Prod |
| Report viewers | Business users of the domain's reports | Viewer on the domain's workspaces, or its app's audience | Prod |
| Data stewards | People who keep the domain's glossary terms and descriptions | No Fabric role (the glossary here, Purview data curator if you use it) | All |

- **Names** come from one pattern for every domain, which platform admins change with **Naming**. The default is `{PREFIX}-{DOMAIN}-{SUBDOMAIN}-{ROLE}-{ENV}` with the prefix `SG-FAB`, so Sales gets `SG-FAB-SALES-READERS-PROD`. An empty part takes its separator with it. Names use letters, digits, `-`, `_` and `.` only, and the mail nickname is the name in lower case (64 characters at most). A role can be turned off so no group is suggested for it.
- **Link existing group** uses a group you already have for a role instead of the suggested one: find it by name, or paste its object ID. Platform admins and the domain's owners (or its parent domain's) can link. Distribution lists are shown but can't be picked: they can't hold Fabric roles. Links are kept in `catalog.entra_group` and on the Audit tab.
- **Scripts:** **Graph PowerShell** and **az CLI** create the suggested groups that don't exist yet, with the domain's owners (those written as emails) as group owners, and leave linked roles out. **By hand (CSV)** lists every group with the steps to build them in the Entra admin center. **Fabric role assignments** is PowerShell that gives each group its domain role (Fabric's admin API, needs a Fabric administrator) and its workspace roles in the workspaces that hold the domain's data products; data readers are added to OneLake data access roles by hand. Every script is safe to run again. **Copy** or **Save to Downloads** (a new file, never replacing one).
- **Check in Entra** reads which suggested groups already exist and whether linked ones are still there.
- KratosForge creates no group and grants no role itself: an Entra admin runs the scripts, signed in as themselves. The Graph PowerShell script connects with `Group.ReadWrite.All` and `User.Read.All` (`Connect-MgGraph`), the az CLI script uses the admin's own `az login` (a Groups Administrator), and **Fabric role assignments** runs `az login` as a Fabric administrator. Those are the admin's own permissions for that session, not permissions KratosForge asks for. Reading groups needs `Group.Read.All` ([Permissions](permissions.md)), which needs an Entra admin's consent; linking by object ID works without it.
- A data product's page shows **Access to the domain**: the readers, viewers and owners groups to ask for.
- After an update, a platform admin runs **Platform > Settings > Finish or repair** once before groups can be linked or the naming saved; until then the dialog uses the default naming.
- **Governance** (on **Govern**): the organization's governance policies, their versions, where each applies and why. See [Governance policies](governance.md).
- **Requests** moved to **Data mesh > Requests**, where everyone sees what they sent and what waits on them; older links to Platform's Requests open it there. See [Requests](#requests) below and [Data mesh](data-mesh.md#requests-preview).
- **Audit** (on **Govern**) and **Settings** (where each environment's platform is, and its admins).

### Owners (Preview)

**Platform > Overview > Owners** lists every domain, published data product and landing zone source that needs attention before it matters at 2am. Each is listed for one of three reasons: it has **no owner**; it has **no second owner** to cover; or an owner **hasn't used the platform** for 60 days (or ever). Nothing without an owner comes first, and **Open** goes to where its owner is set. Owners are read from each item's owners field. An owner written as a name rather than a sign-in (such as *Sales team*) can't be checked for being quiet, and the card says so.

"Used the platform" means the app opened the platform as them (recorded once a day in `catalog.person`), or they made any change in the audit log. Quiet owners are checked only when your organization's policy turns on `platform_presence` ([Azure setup](azure-setup.md)); it is off by default because everyone who reads the catalog can see those dates ([Security](security.md#personal-data-in-the-platforms-catalog)). With it off, the card still lists missing and single owners. No directory is read: KratosForge doesn't ask for Microsoft Graph. A platform made before this shows what to do until a platform admin runs **Finish or repair** once.

## Requests

What only someone else may give is asked for, not taken:

| Request | Sent from | Decided by | Approving |
| --- | --- | --- | --- |
| **A key table** | Platform > Business keys, or Data mesh > Surrogate keys > Use a key table > Request a new one | A platform admin | Creates the key table in every environment |
| **A source** in the landing zone | Platform > Set up > Landing zone > **Request a source** | A platform admin | Registers the source; the central team then lands its files. A request that asks for a Copy job (Preview) makes it first, as the person approving: see [Copy jobs from requests](#copy-jobs-from-requests-preview) |
| **Access to a data product** open only to its domain, or private | Data mesh > Products > Request access | The product's owner (or a platform admin) | Lets the project link to it. The owner still gives read access in OneLake (a data access role on the Product workspace) |
| **A shortcut** | OneLake > Shortcuts > Request a shortcut | A platform admin, or the owner of the data product it points at | Makes the shortcut, as the person approving, then marks the request approved ([shortcuts.md](shortcuts.md#requests-and-shortcut-needs)) |
| **A new workspace** (Preview): its name, domain, capacity, admins and why | Data mesh > Requests > New request | A platform admin | Creates the workspace on its capacity and makes the people (by email or UPN) and groups (by object ID) its admins, as the person approving. A workspace's Fabric domain is set with Fabric's admin API, which KratosForge doesn't ask for: the approver is told to put it in the domain in Fabric's admin portal |
| **Access to a workspace** (Preview): a role (Viewer, Contributor, Member or Admin) for you or a group | Data mesh > Requests > New request | A platform admin | Gives the person or group the role, as the person approving (or raises the role they have: approving never lowers one, so a higher role is kept and the approval says so). The request shows the role that person or group has in the workspace today, read from Fabric as the approver and never stored, so you see before approving whether anything changes; only a role given to them directly counts. The request and its audit row record whether approving gave the role, raised it (and from what) or kept a higher one |
| **A domain** or subdomain (Preview): name, parent, description, owners | Data mesh > Requests > New request | A platform admin | Creates it in Fabric, as the person approving, and keeps it in the platform linked to it, where every team sees it. That needs a Fabric administrator: if Fabric refuses, the request stays waiting, and a Fabric administrator can approve it, or the approver can create it in the platform only (**Domains > New domain** without **Also create it in Fabric**) and approve again. Its Fabric description ends with **(KratosForge request <id>)**, and the text before that is cut to fit Fabric's 256 characters. The platform keeps the description in full. If an approval stopped after Fabric made the domain, approving again keeps that domain and says so. Any other name Fabric already has fails with a hint to **Import from Fabric**. A subdomain whose parent isn't in Fabric is saved in the platform only |
| **An exception to a governance policy** (Preview): the policy, a project, domain or workspace, until when (a year at most) and why | Data mesh > Requests > New request, or a policy's **Where it applies > Ask for one** | A platform admin | Records the exception in the platform's catalog (`catalog.policy_exception`), in the audit and in the policy's history. Where the policy is resolved for that place, it shows **Excepted until <date>: <why>** instead of applying. It ends on its own on its date |
| **Data from another team** (Preview): a new data product from a domain, or a change to a product, what it's for and an optional date it's needed by | Data mesh > Requests > New request, Data mesh > Products > **Ask for data**, or a product's page > **Ask for a change** | The domain's owners for a new product, the product's owner for a change (or a platform admin) | Nothing is made. **Accept** asks for a reply saying how and when the team will do it, and the person who asked sees it on the request. A platform made before this answers that it's older than the app: a platform admin runs **Finish or repair** once |

What an approval makes in Fabric (a workspace, a domain, or a landing source's Copy job) has a description that ends with **(KratosForge request <id>)**, so approving again after a step failed finds it instead of making it twice. The reason before it is cut to fit Fabric's limit (4000 characters for a workspace, 256 for a domain or a Copy job) and the platform keeps it in full. Text that looks like a marker is dropped from the reason first.

Every field is checked when the request is sent: a workspace name Fabric wouldn't take, a role that isn't one, an admin who isn't an email, UPN or object ID, a domain that's already there, a date past a year: the request is refused then, not on approval. Approving makes what was asked for first, as the person approving, and only then marks the request approved, after **Approve** says what will be made. If a step fails, the request stays waiting and the error names the step. A workspace request remembers what it already made (the workspace, and which admins were added), and the workspace's description names the request, so approving again carries on instead of making a second one; a workspace of the same name that the request didn't make is never taken over.

Who may decide a request is checked by the platform itself. For workspace access that's platform admins only for now: the platform runs in Fabric as a user data function and can't safely read a workspace's own admins, and a workspace's admins can give roles themselves on **OneLake > Access**.

After this update, a platform admin runs **Platform > Settings > Finish or repair** once, so `UDF_Platform` knows the new kinds and has `record_request_progress` and `list_policy_exceptions`. Until then, sending one of these says so.

Each request records who sent it, why, who decided and a note. A request can be withdrawn while it waits. If approving a request failed partway, what it already made (a workspace, a role, a Copy job and its schedule, a domain, a shortcut) stays in Fabric when the request is declined or withdrawn: KratosForge deletes nothing. The decline confirm and the message after say what is still there, so you can delete it in Fabric if it isn't wanted, and the audit row records its ids. A product owner or a platform admin can take access away again on the product's page (**Data mesh > Products >** the product **> Access given > Take access away**, Preview), after a confirm. Existing links stay in the project and their runs keep reading, but no new link is allowed until the project asks again. To stop reads too, remove the project's OneLake data access role on the Product workspace.

**Access reviews (Preview).** Every 90 days, a product's owner confirms that each project given access still needs it. **Data mesh > Products** says how many grants on your products haven't been confirmed in that time, and **Review due** shows only those products. On a product's page, **Access given** shows when each grant was last confirmed. **Still needed** (or **All still needed**) records your confirmation in `catalog.grant_review` and the audit log; **Take access away** removes it. Nothing is taken away by itself: a grant nobody confirms stays, marked **Review due**. A platform admin sees every product's grants. A platform made before access reviews says so until a platform admin runs **Finish or repair** once.

### Notices to Teams (Preview)

A request waiting on someone, a decision or withdrawal, a shortcut that breaks or is fixed, a data product changed in Prod (for its followers) or deprecated (for the projects that read it), an incident upstream of a team's data, and a policy exception about to end can each be posted to a Teams channel through a Teams Workflows webhook. A route's kinds are `request.waiting`, `request.decided`, `request.withdrawn`, `shortcut.broken`, `shortcut.fixed`, `product.changed`, `product.deprecated`, `incident.impact` and `exception.ending`. A request declined or withdrawn after its approval failed partway says in its notice that what it made is still there (the workspace, the role, the Copy job, the domain or the shortcut), with no names, so the approvers who can remove it hear of it. Each change is posted once: a shortcut that stays broken isn't posted again, and its fix is posted only if its break was. Routes send some kinds of notice, or some domains, to their own channel; everything else goes to the route named `default`. Quiet hours hold notices until they end, and a notice Teams doesn't take waits on this PC and goes out on the next try, marked as delayed.

People are named by their UPN. A request's reason is never put in a Teams message, which anyone in the channel can read: the message names the request and says where to open it in the app. Webhook addresses work like passwords: the app never shows them again after they're saved, leaves them out of the audit log, and leaves them out of backups.

Routes and quiet hours are set in **Settings > Notifications > Platform notices**, and the bell's **Platform** filter lists what's still open ([Platform notices](user-guide.md#platform-notices-preview)). The requests and shortcut pages don't send notices yet. Email isn't supported yet: it needs a way to send without an app registration.

## One landing zone

Raw data from every source lands once, in `LH_Landing`, organized by source system: `Files/erp/customers/2026-10-16.csv`. **Only the central team writes there**, with pipelines or copy jobs it owns, including the ones a platform admin makes by approving a team's request ([Copy jobs from requests](#copy-jobs-from-requests-preview)); nobody lands the same source twice. Platform admins register each source on **Platform > Landing zone** with its owner, how often it should arrive, and the name of the Key Vault secret its connection uses per environment (secrets themselves never go in the platform). A domain team that needs a new source requests it.

Each team builds its own Bronze from it: **Data mesh > New entity > From the landing zone** picks a source and entity, or an entity reads the landing zone with `landing` instead of `files`:

```toml
[source]
landing = "erp/customers/*.csv"
```

In Test and Prod that reads `LH_Landing` in the environment's Platform workspace. In Dev on a PC it reads `%LOCALAPPDATA%\FabricDesktop\platform\landing-dev\Files`, one folder shared by every project on that PC. Give teams **Viewer** on the Platform workspace, or a OneLake data access role on the landing folders they may read; only the central team gets Contributor or above, so only it can write. When a project joins the mesh, the catalog records which sources it reads, so the Landing zone tab shows each source's readers and a source still read can't be removed.

### Copy jobs from requests (Preview)

A domain team can ask for the Copy job that lands its source, and a platform admin approves it. **Request a source** (Platform > Landing zone) names:

- the **Fabric connection** to read from, picked from the SQL Server connections the person can use. Its credentials stay in Fabric: KratosForge never reads them.
- the **tables**, as `schema.table`, and for each, whether a run copies the whole table or only the rows changed since the last run, by a column such as `ModifiedAt`.
- a **time of day** in UTC for its daily run, or none.

**Approve and make it** says what it makes and where: with one landing workspace, the zone and lakehouse it writes to (the request names the zone; Shared is read by every domain, and by Dev and Test). Then, as the person approving:

1. Creates the Copy job `CJ_Landing_<source>` in the landing workspace (the source's zone's lakehouse with [one landing workspace](#one-landing-workspace-for-dev-test-and-prod-preview); otherwise the approver picks the environment whose LH_Landing it writes to). Each table lands as Parquet in `Files/<source>/<table>/`. Its description names the request.
2. Adds its daily schedule, when the request asked for one.
3. Registers the source, as any approved source request does.

Creating the Copy job starts no run. Each run uses capacity, and the connection's gateway reads the source. If a step fails, the request stays waiting and the error names the step; approving again carries on with the Copy job the request already made. A Copy job of the same name that the request didn't make is never taken over. The source's panel on **Landing zone** then shows **Lands with CJ_Landing_<source>**, and a platform admin can **Run now...** after a confirm and the [risk check](finops/risk-check.md), which says when it couldn't run; the run shows in **Monitor > Runs**. Changing the tables is a new request; the Copy job itself is edited in Fabric (**Data Factory > Copy jobs** reads it).

This is Preview: the Copy job definition follows Microsoft's documentation and hasn't been created on a real tenant yet. Bronze reads the files with **Data mesh > New entity > From the landing zone**.

### One landing workspace for Dev, Test and Prod (Preview)

Raw source data isn't per environment, so the landing zone can be one workspace that Dev, Test and Prod all read, `Platform - Landing` by default, split into **zones**. Each zone is its own lakehouse in that workspace:

| Zone | Lakehouse | Who reads it |
| --- | --- | --- |
| **Shared** | `LH_Landing` | every domain; Dev, Test and Prod |
| A domain's zone, like **HR** | `LH_Landing_HR` | its readers; Dev and Test only if you say so |
| A **secure** zone for PII or PHI, like **HR-Secure** | `LH_Landing_HR_Secure` | its readers only; Prod only by default |

Each source is in one zone (the **Zone** column on **Platform > Landing zone**, and **Zone** in a source's Edit dialog); a source with no zone is in Shared. Zones are recorded in the catalog (`catalog.landing_zone`, and `catalog.source.zone`), and only platform admins add, change or remove one; each change is on the Audit tab.

**Entities don't change.** They keep `landing = "<source>/<entity>/*.csv"`: the platform knows which zone each source is in, so a run reads that zone's lakehouse in the landing workspace, the same one in every environment. Sources in a new zone (HR, HR-Secure and so on) are read there from the start. Shared sources keep reading each environment's own `LH_Landing` until reads are switched (below). Dev on a PC still reads the local landing folder for every zone.

**Dev and Test read a zone only if you say so.** **New landing zone** asks *Dev and Test*: **Don't read it (Prod only)**, the default, or **Read it like Prod**. Shared is read by all three. A Dev or Test run of an entity whose source is in a Prod-only zone stops before reading anything, with *"payroll is in the secure zone HR-Secure, which Dev and Test don't read"*. Build and test such entities with made-up files in Dev's own folder (`files = ...`), or in a zone Dev reads.

**Secure zones.** Tick **Secure** for PII, PHI or other sensitive data, and give its readers and sensitivity label. The app records both, but in this release it doesn't make the OneLake data access role or apply the label: set them in Fabric once the zone's lakehouse is created. On `LH_Landing_<Name>`, add a OneLake data access role that grants Read to the readers you named only, make sure workspace Viewers get nothing on it (they would through the default role), and apply the sensitivity label. The zone's card on the Landing zone tab says what to set until this is automated. Shared can't be secure.

**Moving to it** takes two steps, so a move can never break a Prod run:

1. **Move to one landing workspace.** Without a landing workspace, the Landing zone tab works as before (each environment's own `LH_Landing`) and offers this to platform admins. It asks for the workspace name and capacity, confirms, then creates the workspace and its `LH_Landing` (or uses the ones with those names), and records the Shared zone in the catalog. **Runs don't change yet:** Shared sources keep reading each environment's own `LH_Landing`, exactly as before, and the tab says the Shared zone isn't read yet. New zones you add are read from the landing workspace at once.
2. **Switch reads to the landing workspace.** Once the files runs need are in the landing workspace's `LH_Landing`, a platform admin presses this on the Landing zone tab and confirms: every environment then reads Shared sources from the landing workspace. The switch is recorded on the Shared zone in the catalog (`switched_at`, `switched_by`) and on the Audit tab.

In this release:

- **Files aren't copied.** Files in each environment's `LH_Landing` stay where they are. Copy what runs still need (Prod's especially), and point the central team's pipelines at the landing workspace, before you switch reads.
- **Nothing is deleted.** The old `LH_Landing` lakehouses stay until you remove them.
- In Fabric, if the catalog's `UDF_Platform` is older than landing zones, choose **Finish or repair** for Prod in **Platform > Settings** first, so it has the new functions; until the Shared zone is in the catalog, nothing changes.

Other PCs pick up the landing workspace the first time they open **Platform > Landing zone**; an organization that sets the platform in `policy.json` adds it there as `platform.landing` (`workspace_id`, `workspace_name`, `lakehouse_id`). **New landing zone** creates its lakehouse in the landing workspace and needs the workspace set up first. Creating the workspace and lakehouses uses the Fabric permissions sign-in already asks for (`Workspace.ReadWrite.All`, `Item.ReadWrite.All`), and reading and writing the zones in OneLake uses capacity.

Zones and each source's zone can also be kept in a [platform repository](source-control.md#platform-repository) (`landing_zones/<id>.toml`, and `zone` in `sources/<code>.toml`). Its deploy creates missing zones the same way **New landing zone** does, never removes one, and never switches reads: that stays the manual step above.

## One list of source systems (Preview)

The source systems on **Platform > Landing zone** are the one list for every project. Each has an **id** (a whole number that never changes, used by business keys and loaded rows), a **code** (its folder name in any case, like `SAP` for `Files/sap/`), a name, owner and system type. A project doesn't define its own sources: on **Data mesh > Design > Source systems** it chooses **Add a source this project reads**, picks one from the platform's list, and gives only its own connection in Dev, Test and Prod. The name, id, owner and type stay read only there, with **Edit in Platform**.

**Projects made before this keep their sources.** They show under "Only in this project" and work as before. **Move sources to the platform** copies them into the platform's list with the **same ids**, like Move keys; their connections stay in the project. Only platform admins move sources. A code or id the platform already uses for another source is reported and left alone, because rows already loaded keep the project's id: nothing is renumbered. The tab lists any source whose id differs from the platform's, so a platform admin can decide which stays.

A platform made before this update gets ids for its sources the first time it's read: in name order, after the highest id it has. In Fabric, a platform admin runs **Platform > Settings > Finish or repair** once after updating, so `UDF_Platform` has the new functions (the `catalog.source` table gains the columns `source_id`, `code`, `system_type` and `active`; schema version 5).

## One business key system

Once an environment's platform is in Fabric, every project's surrogate keys in that environment come from `SQL_Platform`. A new project never gets an `LH_Keys` of its own, so Customer ABC has one key across every team. Keys are kept apart per environment until they move into [one keys database](#one-keys-database-for-dev-test-and-prod).

Key tables are the platform's, not a project's. Only platform admins create one (**Platform > Business keys > New key table**, or by approving a request), and it's made in every environment at once. A project picks the ones it uses on **Data mesh > Design > Surrogate keys > Use a key table**. A run that needs a key table the platform doesn't have stops before writing anything and says to request it.

## Moving surrogate keys into the platform

Projects that already issue keys in `LH_Keys` keep them there until you move them. On **Data mesh > Design > Surrogate keys**, a platform admin chooses **Move keys** for Test or Prod (copying keys in with their numbers is for platform admins only). KratosForge:

1. Copies every key into `SQL_Platform` with the **same number**. A business key that already has a different number in the platform stops the move; nothing is renumbered.
2. Reads the copy back and compares it, row by row, with `LH_Keys`.
3. Only if every key table matches, writes `keys = "platform"` for that environment in `environments.toml`.

`LH_Keys` is only read, never changed. From then on each run asks the platform for keys: new business keys get the next number after the highest one moved.

**Back to LH_Keys** sets `keys = "lakehouse"` instead, but only while the platform hasn't issued keys that `LH_Keys` doesn't have. After that, going back would hand out the same numbers twice, so it's refused.

## One keys database for Dev, Test and Prod

!!! note "Preview"
    Built and tested against SQLite, not yet on a live tenant. It's in **Settings > Platform**, on the **Keys** row: **Create** or **Join existing** for the Keys workspace, then **Move keys in**.

A business key can have **one** surrogate key everywhere: Customer ABC is key 123 in Dev, Test and Prod. The keys then live in a dedicated **Keys workspace**, named `KratosForge Keys` unless you choose another name:

| Item | Type | What it's for |
| --- | --- | --- |
| `SQL_Keys` | SQL database | Every key table (`keys.<name>`) and the platform admins who may add one |
| `UDF_Keys` | User data functions | The same functions as `UDF_Platform`, connected to `SQL_Keys` in the same workspace |

Setting it up creates only those two items and makes you its first admin. Nothing moves until a platform admin moves the keys in.

**First, a preview.** **Move keys in** lists every key table in every environment, how many keys each holds, and how many Dev and Test keys would get a different number. It writes nothing until you confirm.

**Then the move:**

1. Every environment's keys **close**. No run can issue a key while they're copied: a run that needs a new key stops and says the keys are moving.
2. Prod's key tables are copied with the **same numbers** and checked row by row.
3. Test's and Dev's follow. A business key Prod already has takes Prod's key. One Prod doesn't have gets a new key. The result counts how many Dev and Test keys changed number.
4. The platform admins of every environment become admins of the keys database, so they can still add key tables. An admin entry that isn't a sign-in name (like `ana`, saved before the platform checked) never matched anyone, so it stays behind. A platform admin is always added by sign-in name (`ana@contoso.com`); groups can't be platform admins.
5. Only when every key table checked out, and no environment's keys changed during the move, does every environment switch to the keys database. Each environment's own keys stay closed, with a note saying where keys are now, so a PC that hasn't joined the Keys workspace can't issue different keys.

Each environment's own keys are only read, never changed or deleted.

**If the move stops** (a network drop, a table that didn't check out), nothing switches and every environment's keys stay closed, so they can't change. Run Move keys in again and the same keys copy again. Or **cancel the move**: after a confirm, it removes what the move copied into the keys database and reopens every environment's keys. Keys that already moved and are in use can't be cancelled or cleared: the switch marks the keys database as live, and from then on it refuses to be filled by another move or emptied, whoever asks. A PC that joins the Keys workspace afterwards sees that mark and uses the shared keys straight away.

In Fabric, each Platform workspace needs **Finish or repair** once after this update, so `UDF_Platform` has the functions that close and reopen keys. A platform that doesn't have them yet refuses the move before anything is copied.

**After the move, re-run Dev and Test.** Their tables still hold the old numbers for any key that changed, so rebuild them before you compare them with Prod. Prod's tables don't change.

From then on:

- A key issued by a run in Dev is the same key in Prod. A test run that meets new business keys uses up key numbers in the shared database.
- **New key table** makes it once, in the keys database. **Platform > Business keys** shows that one list, with no environment picker.
- A project's own `LH_Keys` moves with **Move keys** as before. Prod keeps its numbers. Dev and Test take the shared database's numbers, and the result says how many changed.
- Dev runs on this PC ask the keys database in Fabric for keys, so they need you signed in. Until the Keys workspace is in Fabric, the shared keys live in `%LOCALAPPDATA%\FabricDesktop\platform\keys.sqlite`.

Engineers need **Viewer** on the Keys workspace and **Execute** on `UDF_Keys`, the same access as on `UDF_Platform`. No new sign-in permission is asked for.

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
- The Keys workspace: creating `SQL_Keys` and `UDF_Keys`, and moving every environment's keys into it.
- Importing Fabric's domains with Microsoft's Fabric sign-in: whether it gets `Tenant.Read.All` for a Fabric administrator, and the exact shape of `GET /v1/admin/domains?preview=false`.

If you try it, please say how it went in a [bug report or the Ideas forum](ideas.md).

## Related

- [Data mesh](data-mesh.md): domains, data products and links between teams
- [Working across teams](teams.md)
- [Permissions](permissions.md)
